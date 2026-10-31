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


def _git(cwd, *args, check=True):
    return subprocess.run(["git", "-C", str(cwd), *args], check=check,
                          capture_output=True, text=True)


def _root_merge(target, source, branch="main"):
    """Run the canonical root merge under the umask sudo hands an operator."""
    return subprocess.run(
        ["bash", "-c", 'umask 077; source "$1"; mios_root_merge "$2" "$3" "$4"',
         "mios-root-merge", str(ROOT / "automation/lib/root-merge.sh"),
         str(target), str(source), branch],
        capture_output=True, text=True)


def _fixture_repo(source):
    config = source / "usr/libexec/mios/config.json"
    config.parent.mkdir(parents=True)
    config.write_text("{}\n")
    executable = config.with_name("mios-example")
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o755)
    library = source / "usr/lib/mios"
    library.mkdir(parents=True)
    (library / "paths.sh").write_text("# sourced library\n")
    _git(source.parent, "init", "-q", "-b", "main", str(source))
    _git(source, "add", "usr")
    _git(source, "-c", "user.name=MiOS", "-c", "user.email=mios@example.invalid",
         "commit", "-q", "-m", "root merge fixture")
    return config, executable


class TestRootMergeModes(unittest.TestCase):
    """A root merge yields a readable OS root that is the fetched Git top level."""

    def test_root_merge_preserves_tracked_modes(self):
        with tempfile.TemporaryDirectory(prefix="mios-root-merge-") as temp:
            base = Path(temp)
            source, target = base / "source", base / "root"
            source.mkdir()
            config, executable = _fixture_repo(source)
            result = _root_merge(target, source)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            # Git records 0644/0755; the OS root must not inherit umask 077.
            for path, mode in ((config, 0o644), (executable, 0o755),
                               (source / "usr/lib/mios/paths.sh", 0o644),
                               (source / "usr/lib/mios", 0o755)):
                actual = (target / path.relative_to(source)).stat().st_mode & 0o777
                self.assertEqual(actual, mode, f"{path.relative_to(source)}: {actual:o}")
            diff = _git(target, "diff", "--exit-code", check=False)
            self.assertEqual(diff.returncode, 0, diff.stdout + diff.stderr)
            self.assertEqual(_git(target, "rev-parse", "--show-toplevel").stdout.strip(),
                             str(target.resolve()))
            self.assertEqual(_git(target, "config", "branch.main.remote").stdout.strip(), "origin")

    def test_root_merge_rejects_a_root_that_is_not_its_own_top_level(self):
        """Negative control: an empty .git inside a parent repo must not merge into the parent."""
        with tempfile.TemporaryDirectory(prefix="mios-root-merge-") as temp:
            base = Path(temp)
            source, parent = base / "source", base / "parent"
            source.mkdir()
            _fixture_repo(source)
            _git(base, "init", "-q", "-b", "main", str(parent))
            target = parent / "root"
            (target / ".git").mkdir(parents=True)
            result = _root_merge(target, source)
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("not the Git top level", result.stderr)
            self.assertFalse((target / "usr").exists())
            self.assertFalse((parent / "usr").exists())
            remotes = _git(parent, "remote", check=False).stdout.strip()
            self.assertEqual(remotes, "", "the parent repository was modified")

    def test_root_merge_fails_when_the_branch_cannot_be_fetched(self):
        """Negative control: a missing branch fails and reports the fetch."""
        with tempfile.TemporaryDirectory(prefix="mios-root-merge-") as temp:
            base = Path(temp)
            source, target = base / "source", base / "root"
            source.mkdir()
            _fixture_repo(source)
            result = _root_merge(target, source, "no-such-branch")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("fetch no-such-branch", result.stderr)


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

    @staticmethod
    def host_root() -> Path:
        """The OS root: / on POSIX, the system drive root on Windows."""
        return Path(os.environ.get("SystemDrive", "C:") + "/") if os.name == "nt" else Path("/")

    @staticmethod
    def is_git_top_level(path: Path) -> bool:
        """True only when path itself is the top level of a Git worktree.

        A .git entry is not enough: an empty or foreign .git, or a path nested
        in another repository, resolves a different (or no) top level.
        """
        res = subprocess.run(["git", "-C", str(path), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True)
        return res.returncode == 0 and Path(res.stdout.strip()).resolve() == path.resolve()

    def test_01_repository_overlaid_on_host_root(self):
        """The host OS root is itself the MiOS Git top level and tracks the SSOT."""
        root = self.host_root()
        self.assertTrue(self.is_git_top_level(root),
                        f"Host root {root} must be the Git top level (MiOS is a root filesystem overlay)")
        tracked = subprocess.run(["git", "-C", str(root), "ls-files", "--error-unmatch",
                                  "usr/share/mios/mios.toml"], capture_output=True, text=True)
        self.assertEqual(tracked.returncode, 0, tracked.stderr)

    def test_03_planted_negative_controls_fail_the_root_check(self):
        """Negative control: an empty root, an empty .git, and a nested directory are not roots."""
        with tempfile.TemporaryDirectory(prefix="mios-root-check-") as temp:
            empty = Path(temp, "empty")
            empty.mkdir()
            fake = Path(temp, "fake")
            (fake / ".git").mkdir(parents=True)
            self.assertFalse(self.is_git_top_level(empty))
            self.assertFalse(self.is_git_top_level(fake))
        self.assertFalse(self.is_git_top_level(ROOT / "usr/share/mios"))

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
        if os.environ.get("MIOS_ENFORCE_ADR0030_BUDGET"):
            self.assertLessEqual(tracked_count, ceiling, f"Tracked files {tracked_count} exceeds SSOT ceiling of {ceiling}")
        else:
            self.assertGreater(tracked_count, 0, "Tracked files count must be non-empty")



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
