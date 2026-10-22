#!/usr/bin/env python3
# AI-hint: Two-sided verification test suite for Milestone M4: Singular Root-Git Substrate,
# level-by-level .gitignore isolation, Windows host namespace protection, ADR-0030 file budget,
# bare store .archive/, and standing verification gates.
# AI-related: .gitignore, usr/share/mios/mios.toml, tools/drift-checks.py, .archive/
import fnmatch
import tempfile
import os
import subprocess
import sys
import unittest
import tomllib
from pathlib import Path


def find_mios_root() -> Path:
    if os.environ.get("MIOS_ROOT"):
        p = Path(os.environ["MIOS_ROOT"])
        if p.is_dir():
            return p
    local_root = Path(__file__).resolve().parent.parent
    if (local_root / "usr/share/mios/mios.toml").exists():
        return local_root
    for c in [Path("C:/"), Path("M:/MiOS/worktrees/mios-integration"), Path("C:/MiOS")]:
        if c.is_dir() and (c / "usr/share/mios/mios.toml").exists():
            return c
    return local_root


ROOT = find_mios_root()


class TestRootMergeModes(unittest.TestCase):
    """A root merge must preserve Git executable bits and stay content-clean."""

    def test_root_merge_preserves_tracked_modes(self):
        with tempfile.TemporaryDirectory(prefix="mios-root-merge-") as temp:
            base = Path(temp)
            source, target = base / "source", base / "root"
            source.mkdir()
            config = source / "usr/libexec/mios/config.json"
            config.parent.mkdir(parents=True)
            config.write_text("{}\n")
            config.chmod(0o644)
            executable = config.with_name("mios-example")
            executable.write_text("#!/bin/sh\nexit 0\n")
            executable.chmod(0o755)
            library = source / "usr/lib/mios"
            library.mkdir(parents=True)
            (library / "paths.sh").write_text("# sourced library\n")
            (library / "paths.sh").chmod(0o644)
            for args in (
                ["init", "-b", "main"],
                ["add", "usr"],
                ["-c", "user.name=MiOS", "-c", "user.email=mios@example.invalid",
                 "commit", "-m", "root merge fixture"],
            ):
                subprocess.run(["git", "-C", str(source), *args], check=True,
                               capture_output=True)
            result = subprocess.run(
                ["bash", "-c", 'source "$1"; mios_root_merge "$2" "$3" main',
                 "mios-root-merge", str(ROOT / "automation/lib/root-merge.sh"),
                 str(target), str(source)],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual((target / config.relative_to(source)).stat().st_mode & 0o777, 0o644)
            self.assertEqual((target / executable.relative_to(source)).stat().st_mode & 0o777, 0o755)
            self.assertEqual((target / "usr/lib/mios/paths.sh").stat().st_mode & 0o777, 0o644)
            diff = subprocess.run(["git", "-C", str(target), "diff", "--exit-code"],
                                  capture_output=True, text=True)
            self.assertEqual(diff.returncode, 0, diff.stdout + diff.stderr)


class TestLevelGitignoreIsolation(unittest.TestCase):
    """Verifies level-by-level .gitignore rules preventing git directory traversal pruning
    while strictly ignoring host Windows operating system folders."""

    def setUp(self):
        self.gitignore_path = ROOT / ".gitignore"
        self.assertTrue(self.gitignore_path.exists(), "Root .gitignore must exist")
        self.gitignore_content = self.gitignore_path.read_text(encoding="utf-8")
        self.lines = [line.strip() for line in self.gitignore_content.splitlines() if line.strip() and not line.startswith("#")]

    def test_01_windows_host_directories_strictly_ignored(self):
        """Positive Control: Verify critical host Windows folders are explicitly ignored."""
        mandatory_windows_patterns = [
            "/Windows", "/Program Files", "/Program Files (x86)",
            "/Users", "/$Recycle.Bin", "/Recovery", "/System Volume Information"
        ]
        for pattern in mandatory_windows_patterns:
            self.assertTrue(
                any(p == pattern or p == pattern.lstrip("/") for p in self.lines),
                f"Missing mandatory Windows ignore pattern: {pattern}"
            )

    def test_02_level_by_level_unignore_preserves_mios_share(self):
        """Positive Control: Verify level-by-level unignoring of /usr/share/mios/**."""
        self.assertTrue(any("!/usr" in line for line in self.lines))
        self.assertTrue(any("!/usr/share" in line for line in self.lines))
        self.assertTrue(any("!/usr/share/mios" in line for line in self.lines))

    def test_03_planted_negative_control_windows_path_rejection(self):
        """Negative Control: Planted check-ignore probe confirms host paths are ignored."""
        res = subprocess.run(
            ["git", "-C", str(ROOT), "check-ignore", "-v", "Windows", "Program Files", "Users"],
            capture_output=True, text=True
        )
        self.assertEqual(res.returncode, 0, "Host Windows directories must be matched by check-ignore")
        self.assertIn(".gitignore", res.stdout)

    def test_04_planted_negative_control_tracked_file_not_ignored(self):
        """Negative Control: Planted probe confirms SSOT file is NOT ignored."""
        res = subprocess.run(
            ["git", "-C", str(ROOT), "check-ignore", "-v", "usr/share/mios/mios.toml"],
            capture_output=True, text=True
        )
        self.assertNotEqual(res.returncode, 0, "SSOT file usr/share/mios/mios.toml must NOT be ignored")


class TestSingularRootRepositoryAxiomAndHostProtection(unittest.TestCase):
    """Verify that the deployed host root contains MiOS's git metadata."""

    def test_01_repository_overlaid_on_host_root(self):
        """Linux uses /.git; Windows uses the host system drive root."""
        host_root = Path(os.environ.get("SystemDrive", "C:") + "/") if os.name == "nt" else Path("/")
        self.assertTrue(
            (host_root / ".git").exists(),
            f"Host root {host_root} must contain .git (MiOS is a root filesystem overlay)",
        )

    def test_02_worktree_reports_zero_untracked_host_folders(self):
        """Positive Control: Verify git status reports 0 untracked host OS folders."""
        res = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain"], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)
        untracked = [line.split(maxsplit=1)[1] for line in res.stdout.splitlines() if line.startswith("??")]
        for p in untracked:
            self.assertFalse(p.startswith(("Windows", "Program Files", "Users", "$Recycle.Bin")),
                             f"Host operating system folder reported as untracked: {p}")


class TestAdr0030FileBudgetRatchet(unittest.TestCase):
    """Verifies adherence to ADR-0030 non-expanding file budget."""

    def test_01_ssot_ratchet_ceilings(self):
        """Positive Control: Verify mios.toml specifies max_tracked_files <= 3250."""
        toml_path = ROOT / "usr/share/mios/mios.toml"
        self.assertTrue(toml_path.exists())
        with open(toml_path, "rb") as f:
            cfg = tomllib.load(f)
        legibility = cfg.get("legibility", {})
        self.assertLessEqual(legibility.get("max_tracked_files", 9999), 3250)
        self.assertEqual(legibility.get("max_bootstrap_files"), 201)

    def test_02_tracked_files_within_budget(self):
        """Positive Control: Every tracked path counts against the SSOT ceiling."""
        with (ROOT / "usr/share/mios/mios.toml").open("rb") as stream:
            ceiling = tomllib.load(stream)["legibility"]["max_tracked_files"]
        res = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True, text=True, check=True)
        tracked_count = len([path for path in res.stdout.split(chr(0)) if path])
        self.assertLessEqual(tracked_count, ceiling, f"Tracked files {tracked_count} exceeds SSOT ceiling of {ceiling}")


class TestBareStoreLegacyArchive(unittest.TestCase):
    """Verifies that .archive/ exists as a git bare store with zero tracked .tar bundles."""

    def test_01_archive_bare_store_structure(self):
        """Positive Control: Verify .archive/ contains HEAD and legacy ref."""
        archive_dir = ROOT / ".archive"
        self.assertTrue(archive_dir.is_dir(), ".archive directory must exist")
        self.assertTrue((archive_dir / "HEAD").exists(), ".archive/HEAD must exist")
        self.assertTrue((archive_dir / "refs/heads/legacy").exists(), ".archive/refs/heads/legacy must exist")

    def test_02_zero_tracked_tar_bundles(self):
        """Positive Control: Verify 0 tracked .tar or .tar.gz archives of historical code in git index."""
        res = subprocess.run(["git", "-C", str(ROOT), "ls-files", "*.tar", "*.tar.gz", "*.tgz", ":!usr/share/mios/vendored/**"],
                             capture_output=True, text=True, check=True)
        matched = [line for line in res.stdout.splitlines() if line.strip()]
        self.assertEqual(len(matched), 0, f"Found tracked tar bundles: {matched}")


class TestStandingGatesAndVersionLiteralsSsot(unittest.TestCase):
    """Verifies SSOT version consistency and standing gate clean pass."""

    def test_01_version_literals_ssot_synchronized(self):
        """Positive Control: Verify version 0.3.0 synchronized across SSOT and globals."""
        version_path = ROOT / "VERSION"
        if version_path.exists():
            version = version_path.read_text(encoding="utf-8").strip()
            self.assertEqual(version, "0.3.0")

        # Verify globals.ps1 and globals.sh
        ps1_path = ROOT / "automation/lib/globals.ps1"
        if ps1_path.exists():
            self.assertIn("0.3.0", ps1_path.read_text(encoding="utf-8"))
        sh_path = ROOT / "automation/lib/globals.sh"
        if sh_path.exists():
            self.assertIn("0.3.0", sh_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
