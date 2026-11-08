#!/usr/bin/env python3
# AI-hint: Two-sided verification suite for Milestone M4: Singular Root-Git Substrate,
# level-by-level .gitignore isolation, Windows host namespace protection, ADR-0030 file budget,
# bare store .archive/, the SKILLS archive, and standing verification gates.
# AI-related: .gitignore, usr/share/mios/mios.toml, tools/drift-checks.py, .archive/, SKILLS/MANIFEST.json, usr/lib/greenboot/check/wanted.d/15-mios-root-git.sh
import hashlib
import io
import json
import shutil
import tarfile
import tempfile
import os
import subprocess
import sys
import unittest
import tomllib
from pathlib import Path, PurePosixPath
from unittest import mock


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


# --- The root overlay and the deployed-root axiom ---------------------------
SSOT_PATH = "usr/share/mios/mios.toml"
# The deployed-host gate that owns the '/'-is-the-Git-top-level axiom (greenboot, advisory).
ROOT_GIT_GATE = "usr/lib/greenboot/check/wanted.d/15-mios-root-git.sh"


def is_git_top_level(path: Path) -> bool:
    """True only when path itself is the top level of a Git worktree.

    A .git entry is not enough: an empty or foreign .git, or a path nested
    in another repository, resolves a different (or no) top level.
    """
    res = subprocess.run(["git", "-C", str(path), "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True)
    return res.returncode == 0 and Path(res.stdout.strip()).resolve() == path.resolve()


def overlay_layout_defects(path: Path) -> list:
    """Why path is not a MiOS root overlay ([] when it is): its own Git top level, tracking the
    vendor SSOT at the FHS path it is deployed to."""
    if not is_git_top_level(path):
        return [f"{path} is not its own Git top level"]
    tracked = subprocess.run(["git", "-C", str(path), "ls-files", "--error-unmatch", "--", SSOT_PATH],
                             capture_output=True, text=True)
    if tracked.returncode != 0:
        return [f"{path} does not track the vendor SSOT {SSOT_PATH}: {tracked.stderr.strip()}"]
    return []


def container_kind() -> str:
    """The container this process runs in, '' on a host. WSL is a host, not a container."""
    if os.environ.get("container"):
        return os.environ["container"]
    for marker, kind in ((Path("/run/.containerenv"), "podman"), (Path("/.dockerenv"), "docker")):
        if marker.exists():
            return kind
    try:
        kind = Path("/run/systemd/container").read_text(encoding="utf-8").strip()
    except OSError:
        kind = ""
    if kind and kind != "wsl":
        return kind
    try:
        probe = subprocess.run(["systemd-detect-virt", "--container"], capture_output=True, text=True)
    except OSError:
        return ""
    kind = probe.stdout.strip()
    return kind if probe.returncode == 0 and kind not in ("", "none", "wsl") else ""


def deployed_root_reason(root: Path, container: str) -> str:
    """'' when root is a deployed MiOS root, where the axiom applies; otherwise why it does not."""
    if container:
        return f"running in a {container} container: {root} is the container image, not a deployed host"
    if not (root / SSOT_PATH).is_file():
        return f"{root} carries no MiOS vendor SSOT ({SSOT_PATH}): it is not a deployed MiOS root"
    return ""


def _run_root_gate(root: Path, gate: Path = ROOT / ROOT_GIT_GATE, env=None):
    return subprocess.run(["bash", str(gate), str(root)], capture_output=True, text=True, env=env)


def _ssot_repo(path: Path, track_ssot: bool = True) -> Path:
    """A committed fixture root; it tracks the vendor SSOT at its FHS path unless told not to."""
    tracked = path / (SSOT_PATH if track_ssot else "usr/libexec/mios/config.json")
    tracked.parent.mkdir(parents=True)
    tracked.write_text("# fixture\n")
    _git(path.parent, "init", "-q", "-b", "main", str(path))
    _git(path, "add", "usr")
    _git(path, "-c", "user.name=MiOS", "-c", "user.email=mios@example.invalid",
         "commit", "-q", "-m", "root fixture")
    return path


# --- The one documented, generated archive ----------------------------------
# Canonical archive inventory: its publication record names the only tracked tarball outside vendored/.
ARCHIVE_INVENTORY = "SKILLS/MANIFEST.json"
TAR_PATHSPECS = ("*.tar", "*.tar.gz", "*.tgz", ":!usr/share/mios/vendored/**")


def _tracked(root: Path, *pathspecs) -> list:
    res = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "--", *pathspecs],
                         capture_output=True, text=True, check=True)
    return [path for path in res.stdout.split("\0") if path]


def archive_publication(root: Path) -> dict:
    """The inventory's publication record, {} when the tree carries no inventory."""
    path = root / ARCHIVE_INVENTORY
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8")).get("publication") or {}


def undocumented_tar_bundles(root: Path) -> list:
    """Tracked tarballs outside the vendored store that the inventory does not publish."""
    documented = archive_publication(root).get("archive_path")
    return [path for path in _tracked(root, *TAR_PATHSPECS) if path != documented]


def archive_drift(root: Path) -> list:
    """Every way the documented archive differs from the tracked tree it packages; [] when in sync.

    The archive holds exactly the tracked files of its repository directory except itself and its
    checksum, byte for byte; the checksum file matches the archive; SHA256SUMS matches the payload.
    """
    pub = archive_publication(root)
    directory, archive, checksum = (pub.get(key, "") for key in
                                    ("repository_directory", "archive_path", "checksum_path"))
    if not (directory and archive and checksum):
        return [f"{ARCHIVE_INVENTORY}: publication must name repository_directory, archive_path and checksum_path"]
    prefix = directory.rstrip("/") + "/"
    tracked = set(_tracked(root, prefix))
    defects = [f"{path}: documented by {ARCHIVE_INVENTORY} but not a tracked file of {prefix}"
               for path in (archive, checksum) if path not in tracked]
    if defects:
        return defects
    data = (root / archive).read_bytes()
    expected = [hashlib.sha256(data).hexdigest(), PurePosixPath(archive).name]
    recorded = (root / checksum).read_text(encoding="utf-8").split()
    if recorded != expected:
        defects.append(f"{checksum}: records {recorded}, the archive is {expected}")
    payload = tracked - {archive, checksum}
    members = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as bundle:
            for info in bundle.getmembers():
                if not info.isreg():
                    defects.append(f"{archive}: member {info.name} is not a regular file")
                elif info.name in members:
                    defects.append(f"{archive}: member {info.name} appears twice")
                else:
                    members[info.name] = bundle.extractfile(info).read()
    except (tarfile.TarError, OSError, EOFError) as error:
        return defects + [f"{archive}: not a readable gzip tar archive: {error}"]
    defects += [f"{archive}: member {name} is not a tracked file of {prefix}" for name in sorted(set(members) - payload)]
    defects += [f"{archive}: omits tracked {name}" for name in sorted(payload - set(members))]
    defects += [f"{archive}: member {name} differs from the tracked file" for name in sorted(payload & set(members))
                if members[name] != (root / name).read_bytes()]
    sums_path = prefix + "SHA256SUMS"
    if sums_path in payload:
        sums = {}
        for line in (root / sums_path).read_text(encoding="utf-8").splitlines():
            digest, _, rel = line.partition("  ")
            sums[prefix + rel] = digest
        covered = payload - {sums_path}
        defects += [f"{sums_path}: lists {name}, which is not in the payload" for name in sorted(set(sums) - covered)]
        defects += [f"{sums_path}: omits {name}" for name in sorted(covered - set(sums))]
        defects += [f"{sums_path}: stale digest for {name}" for name in sorted(covered & set(sums))
                    if hashlib.sha256((root / name).read_bytes()).hexdigest() != sums[name]]
    return defects


def _pack_archive(root: Path) -> None:
    """Regenerate a fixture's documented archive and checksum from its tracked payload."""
    pub = archive_publication(root)
    prefix = pub["repository_directory"].rstrip("/") + "/"
    payload = sorted(set(_tracked(root, prefix)) - {pub["archive_path"], pub["checksum_path"]})
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as bundle:
        for name in payload:
            bundle.add(str(root / name), arcname=name)
    (root / pub["archive_path"]).write_bytes(buffer.getvalue())
    digest = hashlib.sha256(buffer.getvalue()).hexdigest()
    (root / pub["checksum_path"]).write_text(f"{digest}  {PurePosixPath(pub['archive_path']).name}\n")


def _archive_fixture(base: Path) -> Path:
    """A tree shaped like SKILLS/: an inventory, a payload, SHA256SUMS, and its archive, all tracked."""
    root = base / "tree"
    skills = root / "SKILLS"
    (skills / "definitions/demo").mkdir(parents=True)
    (skills / "definitions/demo/SKILL.md").write_text("# demo skill\n")
    (skills / "README.md").write_text("demo export\n")
    (skills / "MANIFEST.json").write_text(json.dumps({"publication": {
        "repository_directory": "SKILLS", "branch": "main",
        "archive_path": "SKILLS/demo-skills.tar.gz", "checksum_path": "SKILLS/demo-skills.tar.gz.sha256"}}))
    (skills / "SHA256SUMS").write_text("".join(
        f"{hashlib.sha256((skills / rel).read_bytes()).hexdigest()}  {rel}\n"
        for rel in ("MANIFEST.json", "README.md", "definitions/demo/SKILL.md")))
    (skills / "demo-skills.tar.gz").write_bytes(b"")
    (skills / "demo-skills.tar.gz.sha256").write_text("")
    _git(base, "init", "-q", "-b", "main", str(root))
    _git(root, "add", "SKILLS")
    _pack_archive(root)
    return root


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
    """The checkout is a root overlay. The deployed-root axiom (gate: ROOT_GIT_GATE) is asserted only on a
    deployed root; anywhere else it is skipped with the reason, never counted as a pass."""

    @staticmethod
    def host_root() -> Path:
        """The OS root: / on POSIX, the system drive root on Windows."""
        return Path(os.environ.get("SystemDrive", "C:") + "/") if os.name == "nt" else Path("/")

    def test_01_checkout_is_a_root_overlay(self):
        """The checkout is its own Git top level and tracks the SSOT at its deployed FHS path."""
        self.assertEqual(overlay_layout_defects(ROOT), [],
                         "MiOS is a root filesystem overlay: the checkout root is the overlay root")

    def test_03_planted_negative_controls_fail_the_root_check(self):
        """Negative control: an empty root, an empty .git, a nested directory and an SSOT-less repo are not roots."""
        with tempfile.TemporaryDirectory(prefix="mios-root-check-") as temp:
            empty = Path(temp, "empty")
            empty.mkdir()
            fake = Path(temp, "fake")
            (fake / ".git").mkdir(parents=True)
            self.assertFalse(is_git_top_level(empty))
            self.assertFalse(is_git_top_level(fake))
            self.assertIn("not its own Git top level", overlay_layout_defects(empty)[0])
            self.assertIn("not its own Git top level", overlay_layout_defects(fake)[0])
            bare = _ssot_repo(Path(temp, "no-ssot"), track_ssot=False)
            self.assertTrue(is_git_top_level(bare))
            self.assertIn("does not track the vendor SSOT", overlay_layout_defects(bare)[0])
            self.assertEqual(overlay_layout_defects(_ssot_repo(Path(temp, "overlay"))), [])
        self.assertFalse(is_git_top_level(ROOT / "usr/share/mios"))
        self.assertIn("not its own Git top level", overlay_layout_defects(ROOT / "usr/share/mios")[0])

    def test_02_worktree_reports_zero_untracked_host_folders(self):
        """Positive Control: Verify git status reports 0 untracked host OS folders."""
        res = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain"], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)
        untracked = [line.split(maxsplit=1)[1] for line in res.stdout.splitlines() if line.startswith("??")]
        for p in untracked:
            self.assertFalse(p.startswith(("Windows", "Program Files", "Users", "$Recycle.Bin")),
                             f"Host operating system folder reported as untracked: {p}")

    def test_04_deployed_root_is_the_git_top_level(self):
        """On a deployed MiOS root the OS root is the Git top level tracking the SSOT, and its gate agrees."""
        root = self.host_root()
        reason = deployed_root_reason(root, container_kind())
        if reason:
            self.skipTest(f"deployed-root axiom does not apply: {reason}")
        self.assertEqual(overlay_layout_defects(root), [],
                         f"Host root {root} must be the Git top level (MiOS is a root filesystem overlay)")
        if os.name != "nt":
            gate = _run_root_gate(root)
            self.assertEqual(gate.returncode, 0, gate.stdout + gate.stderr)
            self.assertIn(f"OK: {root} is the MiOS Git top level", gate.stdout)

    def test_05_deployed_root_gate_two_sided(self):
        """The greenboot gate passes an overlay root and fails each planted non-root, as the unit check does."""
        if os.name == "nt":
            self.skipTest("the deployed-root gate is a greenboot check; greenboot runs on Linux hosts only")
        gate = ROOT / ROOT_GIT_GATE
        self.assertTrue(gate.is_file(), f"{ROOT_GIT_GATE} must exist")
        self.assertTrue(os.access(gate, os.X_OK), f"{ROOT_GIT_GATE} must be executable: greenboot skips it otherwise")
        mode = _git(ROOT, "ls-files", "-s", "--", ROOT_GIT_GATE).stdout.split(" ", 1)[0]
        self.assertEqual(mode, "100755", f"{ROOT_GIT_GATE} must be tracked as executable (git mode {mode or 'untracked'})")
        with tempfile.TemporaryDirectory(prefix="mios-root-gate-") as temp:
            empty = Path(temp, "empty")
            empty.mkdir()
            fake = Path(temp, "fake")
            (fake / ".git").mkdir(parents=True)
            overlay = _ssot_repo(Path(temp, "overlay"))
            cases = {
                overlay: "OK: ",
                _ssot_repo(Path(temp, "no-ssot"), track_ssot=False): "does not track the vendor SSOT",
                empty: "is not the MiOS Git top level",
                fake: "is not the MiOS Git top level",
                overlay / "usr/share/mios": "is not the MiOS Git top level",
            }
            for root, message in cases.items():
                with self.subTest(root=root.name):
                    result = _run_root_gate(root)
                    self.assertIn(message, result.stdout, result.stdout + result.stderr)
                    self.assertEqual(result.returncode == 0, message == "OK: ", result.stdout + result.stderr)
                    self.assertEqual(result.returncode == 0, overlay_layout_defects(root) == [],
                                     "the gate and the unit check must agree")

    def test_06_deployed_root_detector_two_sided(self):
        """A MiOS root on a host applies; a container image or a root without the vendor SSOT does not."""
        with tempfile.TemporaryDirectory(prefix="mios-root-detect-") as temp:
            mios_root = Path(temp, "mios")
            (mios_root / SSOT_PATH).parent.mkdir(parents=True)
            (mios_root / SSOT_PATH).write_text("# fixture\n")
            foreign = Path(temp, "foreign")
            foreign.mkdir()
            self.assertEqual(deployed_root_reason(mios_root, ""), "")
            self.assertIn("docker container", deployed_root_reason(mios_root, "docker"))
            self.assertIn("podman container", deployed_root_reason(mios_root, "podman"))
            self.assertIn("not a deployed MiOS root", deployed_root_reason(foreign, ""))
        with mock.patch.dict(os.environ, {"container": "oci"}):
            self.assertEqual(container_kind(), "oci")

    def test_07_axiom_is_skipped_only_where_it_does_not_apply(self):
        """Two-sided: a container or foreign root is skipped with its reason (not passed); a deployed root
        that is not the Git top level fails; a deployed overlay root passes."""
        if os.name == "nt":
            self.skipTest("the fixture roots are POSIX paths checked by the greenboot gate")
        module = sys.modules[__name__]
        with tempfile.TemporaryDirectory(prefix="mios-root-axiom-") as temp:
            foreign = Path(temp, "foreign")
            foreign.mkdir()
            unmerged = Path(temp, "unmerged")
            (unmerged / SSOT_PATH).parent.mkdir(parents=True)
            (unmerged / SSOT_PATH).write_text("# fixture\n")
            overlay = _ssot_repo(Path(temp, "overlay"))
            cases = (
                ("docker", self.host_root(), "skipped", "running in a docker container"),
                ("", foreign, "skipped", "not a deployed MiOS root"),
                ("", unmerged, "failed", "must be the Git top level"),
                ("", overlay, "passed", ""),
            )
            for kind, root, outcome, evidence in cases:
                with self.subTest(outcome=outcome, root=root.name or str(root)), \
                        mock.patch.object(module, "container_kind", return_value=kind), \
                        mock.patch.object(type(self), "host_root", staticmethod(lambda root=root: root)):
                    result = unittest.TestResult()
                    type(self)("test_04_deployed_root_is_the_git_top_level").run(result)
                    observed = ("skipped" if result.skipped else "failed" if result.failures or result.errors
                                else "passed")
                    detail = [text for _, text in result.skipped + result.failures + result.errors]
                    self.assertEqual((result.testsRun, observed), (1, outcome), detail)
                    if evidence:
                        self.assertIn(evidence, detail[0])
                    if outcome == "skipped":
                        self.assertIn("deployed-root axiom does not apply", detail[0])

    def test_08_deployed_root_gate_lints_clean(self):
        """[drift.lint].shell_directories skips usr/lib/greenboot, so the gate is shellchecked here at the
        changed-shell bar ([drift.lint].shell_changed_severity, warning); a planted SC2034 must fail."""
        shellcheck = shutil.which("shellcheck")
        if not shellcheck:
            self.assertNotEqual(os.environ.get("MIOS_DRIFT_REQUIRE_TOOLS"), "1",
                                "shellcheck is required under MIOS_DRIFT_REQUIRE_TOOLS=1 and is not installed")
            self.skipTest("shellcheck is not installed: the gate's lint was not measured")
        source = (ROOT / ROOT_GIT_GATE).read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory(prefix="mios-root-gate-lint-") as temp:
            for name, body, clean in (("gate", source, True),
                                      ("planted", source.replace("set -euo pipefail\n",
                                                                 "set -euo pipefail\nplanted_unused=1\n", 1), False)):
                with self.subTest(name):
                    self.assertEqual("planted_unused=1" in body, not clean)
                    path = Path(temp, name + ".sh")
                    path.write_text(body, encoding="utf-8")
                    res = subprocess.run([shellcheck, "--severity=warning", str(path)], capture_output=True, text=True)
                    self.assertEqual(res.returncode == 0, clean, res.stdout + res.stderr)
                    if not clean:
                        self.assertIn("SC2034", res.stdout)

    def test_09_deployed_root_gate_trusts_the_root_it_checks(self):
        """Greenboot runs as root over a root another user may own: like mios_root_merge, the gate passes
        -c safe.directory for the root it checks. Planted: the same gate without it fails the overlay."""
        if os.name == "nt":
            self.skipTest("the deployed-root gate is a greenboot check; greenboot runs on Linux hosts only")
        # Git's own test hook makes every repository look foreign-owned; no ambient safe.directory applies.
        env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
               "GIT_TEST_ASSUME_DIFFERENT_OWNER": "1"}
        trust = '-c safe.directory="$real" '
        source = (ROOT / ROOT_GIT_GATE).read_text(encoding="utf-8")
        self.assertEqual(source.count(trust), 1, f"{ROOT_GIT_GATE} must trust the root it checks")
        with tempfile.TemporaryDirectory(prefix="mios-root-gate-owner-") as temp:
            overlay = _ssot_repo(Path(temp, "overlay"))
            plain = subprocess.run(["git", "-C", str(overlay), "rev-parse", "--show-toplevel"],
                                   capture_output=True, text=True, env=env)
            self.assertNotEqual(plain.returncode, 0, "precondition: plain git must refuse a foreign-owned root")
            self.assertIn("dubious ownership", plain.stderr)
            trusted = _run_root_gate(overlay, env=env)
            self.assertEqual(trusted.returncode, 0, trusted.stdout + trusted.stderr)
            self.assertIn("OK: ", trusted.stdout)
            bare = _run_root_gate(_ssot_repo(Path(temp, "no-ssot"), track_ssot=False), env=env)
            self.assertNotEqual(bare.returncode, 0)
            self.assertIn("does not track the vendor SSOT", bare.stdout)
            planted = Path(temp, "untrusting-gate.sh")
            planted.write_text(source.replace(trust, ""), encoding="utf-8")
            refused = _run_root_gate(overlay, gate=planted, env=env)
            self.assertNotEqual(refused.returncode, 0, "a gate without safe.directory must fail a foreign-owned root")
            self.assertIn("is not the MiOS Git top level", refused.stdout)


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
    """Verifies that .archive/ exists as a git bare store and that no tracked .tar bundle exists beyond
    the one archive the canonical inventory (SKILLS/MANIFEST.json) publishes, which must match its tree."""

    def test_01_archive_bare_store_structure(self):
        """Positive Control: Verify .archive/ contains HEAD and legacy ref."""
        archive_dir = ROOT / ".archive"
        self.assertTrue(archive_dir.is_dir(), ".archive directory must exist")
        self.assertTrue((archive_dir / "HEAD").exists(), ".archive/HEAD must exist")
        self.assertTrue((archive_dir / "refs/heads/legacy").exists(), ".archive/refs/heads/legacy must exist")

    def test_02_zero_tracked_tar_bundles(self):
        """Positive Control: no tracked .tar/.tar.gz/.tgz outside the vendored store except the documented archive."""
        self.assertEqual(undocumented_tar_bundles(ROOT), [], "Found tracked tar bundles the inventory does not publish")
        documented = archive_publication(ROOT).get("archive_path")
        allowed = [path for path in _tracked(ROOT, *TAR_PATHSPECS) if path == documented]
        if allowed:
            # Allowed only while it is the generated packaging of its tracked tree.
            self.assertEqual(archive_drift(ROOT), [], f"{documented} has drifted from the tree it packages")

    def test_03_documented_archive_matches_its_tree(self):
        """Drift check: the published archive is exactly its tracked directory, and its checksums hold."""
        pub = archive_publication(ROOT)
        self.assertEqual(pub.get("archive_path"), "SKILLS/mios-global-skills.tar.gz",
                         f"{ARCHIVE_INVENTORY} must publish the documented SKILLS archive")
        self.assertEqual(archive_drift(ROOT), [])

    def test_04_planted_negative_controls_fail_the_archive_allowance(self):
        """Negative controls: an undocumented tarball anywhere, a stale payload, a missing or extra
        member, a stale checksum, and an undocumented inventory each fail; the regenerated fixture passes."""
        with tempfile.TemporaryDirectory(prefix="mios-archive-drift-") as temp:
            root = _archive_fixture(Path(temp))
            self.assertEqual(undocumented_tar_bundles(root), [])
            self.assertEqual(archive_drift(root), [], "the generated fixture must pass GREEN")

            # No broad exemption: a second tarball beside the documented one, or elsewhere, is reported.
            for extra in ("SKILLS/other-skills.tar.gz", "docs/bundle.tgz", "SKILLS/definitions/demo/x.tar"):
                (root / extra).parent.mkdir(parents=True, exist_ok=True)
                (root / extra).write_bytes((root / "SKILLS/demo-skills.tar.gz").read_bytes())
                _git(root, "add", extra)
            self.assertEqual(sorted(undocumented_tar_bundles(root)),
                             ["SKILLS/definitions/demo/x.tar", "SKILLS/other-skills.tar.gz", "docs/bundle.tgz"])
            _git(root, "rm", "-q", "--cached", "SKILLS/other-skills.tar.gz", "docs/bundle.tgz",
                 "SKILLS/definitions/demo/x.tar")
            for extra in ("SKILLS/other-skills.tar.gz", "docs/bundle.tgz", "SKILLS/definitions/demo/x.tar"):
                (root / extra).unlink()

            readme = root / "SKILLS/README.md"
            readme.write_text("edited after packing\n")
            defects = archive_drift(root)
            self.assertIn("SKILLS/demo-skills.tar.gz: member SKILLS/README.md differs from the tracked file", defects)
            self.assertIn("SKILLS/SHA256SUMS: stale digest for SKILLS/README.md", defects)
            readme.write_text("demo export\n")
            self.assertEqual(archive_drift(root), [])

            added = root / "SKILLS/definitions/new/SKILL.md"
            added.parent.mkdir(parents=True)
            added.write_text("# new\n")
            _git(root, "add", "SKILLS/definitions/new/SKILL.md")
            defects = archive_drift(root)
            self.assertIn("SKILLS/demo-skills.tar.gz: omits tracked SKILLS/definitions/new/SKILL.md", defects)
            self.assertIn("SKILLS/SHA256SUMS: omits SKILLS/definitions/new/SKILL.md", defects)

            # Repacking with the new member fixes the archive; a stale checksum is still caught.
            checksum = root / "SKILLS/demo-skills.tar.gz.sha256"
            stale = checksum.read_text()
            _pack_archive(root)
            checksum.write_text(stale)
            defects = archive_drift(root)
            self.assertTrue(any(d.startswith("SKILLS/demo-skills.tar.gz.sha256: records") for d in defects), defects)
            self.assertNotIn("SKILLS/demo-skills.tar.gz: omits tracked SKILLS/definitions/new/SKILL.md", defects)

            _git(root, "rm", "-q", "--cached", "SKILLS/definitions/new/SKILL.md")
            added.unlink()
            defects = archive_drift(root)
            self.assertIn("SKILLS/demo-skills.tar.gz: member SKILLS/definitions/new/SKILL.md is not a tracked file of SKILLS/",
                          defects)

            _pack_archive(root)
            self.assertEqual(archive_drift(root), [])
            # Without the inventory's publication record the same archive is an undocumented bundle.
            manifest = root / ARCHIVE_INVENTORY
            manifest.write_text(json.dumps({"publication": {}}))
            self.assertEqual(undocumented_tar_bundles(root), ["SKILLS/demo-skills.tar.gz"])
            self.assertIn("publication must name", archive_drift(root)[0])


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
