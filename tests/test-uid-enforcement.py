#!/usr/bin/env python3
# AI-hint: Unit and regression test suite for test-uid-enforcement functionality.
# AI-functions: test_generate_sysusers_remediation, test_audit_user_environment_mock_valid, test_audit_user_environment_mock_system_uid, test_subuid_subgid_parsing, TestUidEnforcement, TestIdentityGuard, TestRootExtractOwner, TestMiosAccountPrivilege

"""
Unit Test Suite for MiOS UID 1000 Enforcement & Systemd User Session Boundary.
Implements task_909 / task_3142.
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "usr", "libexec", "mios", "user")))
try:
    import uid_enforce
except ImportError:
    # fallback direct import
    import importlib.util
    spec = importlib.util.spec_from_file_location("uid_enforce", os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "usr", "libexec", "mios", "user", "uid_enforce.py")))
    uid_enforce = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(uid_enforce)

class TestIdentityGuard(unittest.TestCase):
    """The MiOS login is the vendor/host [identity].username, resolved through one guard, never uid 1000."""

    ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    WINDOWS = os.path.join(ROOT, "usr", "share", "mios", "windows")
    CALLERS = ("mios-native-client-setup.ps1", "mios-claude-mcp-setup.ps1")

    def probe(self, name):
        import re
        with open(os.path.join(self.WINDOWS, name), encoding="utf-8") as fh:
            match = re.search(r"^\s*\$identityProbe = '((?:[^']|'')*)'\s*$", fh.read(), re.M)
        self.assertIsNotNone(match, f"{name} carries no $identityProbe")
        return match.group(1).replace("''", "'")

    def test_no_uid_1000_lookup_in_windows_setups(self):
        for name in sorted(os.listdir(self.WINDOWS)):
            if name.endswith(".ps1"):
                with open(os.path.join(self.WINDOWS, name), encoding="utf-8") as fh:
                    self.assertNotIn("getpwuid(1000)", fh.read(), f"{name} resolves the login by uid")

    def test_one_guard_in_every_caller(self):
        self.assertEqual(len({self.probe(name) for name in self.CALLERS}), 1, "the callers' guards differ")

    @unittest.skipUnless(os.name == "posix", "the guard runs on the Linux builder")
    def test_guard_accepts_only_a_login_account(self):
        import pwd, subprocess, tempfile
        sys.path.insert(0, os.path.join(self.ROOT, "usr", "lib", "mios"))
        import mios_toml
        uid_min = mios_toml._uid_min()
        login = lambda e: e.pw_uid != 0 and e.pw_uid >= uid_min and not e.pw_shell.endswith(("nologin", "false"))
        accounts = pwd.getpwall()
        good = next((e.pw_name for e in accounts if login(e)), None)
        system = next((e.pw_name for e in accounts if 0 < e.pw_uid < uid_min), None)
        nologin = next((e.pw_name for e in accounts if e.pw_uid >= uid_min and not login(e)), None)
        probe = self.probe(self.CALLERS[0])
        with tempfile.TemporaryDirectory() as tmp:
            vendor = os.path.join(tmp, "mios.toml")
            env = dict(os.environ, MIOS_VENDOR_TOML=vendor, MIOS_HOST_TOML=os.path.join(tmp, "absent.toml"),
                       MIOS_VENDOR_TOML_D=os.path.join(tmp, "v.d"), MIOS_HOST_TOML_D=os.path.join(tmp, "h.d"),
                       PYTHONPATH=os.path.join(self.ROOT, "usr", "lib", "mios"))
            for key in ("MIOS_TOML_ROOT", "MIOS_TOML", "MIOS_USER_TOML"):
                env.pop(key, None)
            def run(name):
                with open(vendor, "w", encoding="utf-8") as fh:
                    fh.write(f'[identity]\nusername = "{name}"\n')
                return subprocess.run([sys.executable, "-c", probe], env=env, capture_output=True, text=True)
            for refused in ("root", "", "no-such-mios-account", system, nologin):
                if refused is None:
                    continue
                res = run(refused)
                self.assertEqual(res.returncode, 1, f"{refused!r} must be refused")
                self.assertIn("[identity].username", res.stderr)
                self.assertEqual(res.stdout, "")
            if good is None:
                self.skipTest("this host has no login account to accept")
            res = run(good)
            self.assertEqual((res.returncode, res.stdout.strip()), (0, good), res.stderr)

class TestUidEnforcement(unittest.TestCase):
    def test_generate_sysusers_remediation(self):
        conf = uid_enforce.generate_sysusers_remediation("mios", 1000)
        self.assertIn("g mios 1000", conf)
        self.assertIn("u mios 1000:mios", conf)
        self.assertIn("m mios wheel", conf)
        self.assertIn("m mios video", conf)

    def test_audit_user_environment_mock_valid(self):
        with patch.object(uid_enforce, "check_user_uid", return_value={
            "exists": True, "username": "mios", "uid": 1000, "gid": 1000,
            "home": "/var/home/mios", "shell": "/bin/bash",
            "is_system_uid": False, "valid_uid": True, "valid_gid": True
        }), patch.object(uid_enforce, "check_subuid_subgid", return_value={
            "subuid_valid": True, "subuid_start": 100000, "subuid_count": 65536,
            "subgid_valid": True, "subgid_start": 100000, "subgid_count": 65536
        }):
            audit = uid_enforce.audit_user_environment("mios")
            self.assertEqual(audit["status"], "PASS")
            self.assertEqual(len(audit["issues"]), 0)

    def test_audit_user_environment_mock_system_uid(self):
        with patch.object(uid_enforce, "check_user_uid", return_value={
            "exists": True, "username": "mios", "uid": 992, "gid": 992,
            "home": "/var/home/mios", "shell": "/bin/bash",
            "is_system_uid": True, "valid_uid": False, "valid_gid": False
        }), patch.object(uid_enforce, "check_subuid_subgid", return_value={
            "subuid_valid": False, "subuid_start": None, "subuid_count": 0,
            "subgid_valid": False, "subgid_start": None, "subgid_count": 0
        }):
            audit = uid_enforce.audit_user_environment("mios")
            self.assertEqual(audit["status"], "FAIL")
            self.assertTrue(any("system UID 992" in issue for issue in audit["issues"]))

    def test_subuid_subgid_parsing(self):
        res = uid_enforce.check_subuid_subgid("nonexistent_user_xyz")
        self.assertFalse(res["subuid_valid"])

    def test_sysusers_consolidation_and_security_groups(self):
        root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        conf_50 = os.path.join(root_dir, "usr", "lib", "sysusers.d", "50-mios.conf")

        self.assertTrue(os.path.isfile(conf_50), f"Missing {conf_50}")

        with open(conf_50, "r", encoding="utf-8") as f:
            content_50 = f.read()

        # Must declare GECOS "'MiOS' User"
        expected_u = 'u mios 1000:mios "\'MiOS\' User" /var/home/mios /bin/bash'
        self.assertIn(expected_u, content_50)

        # Must include all supplementary security groups
        security_groups = ["mios-hermes", "mios-ai", "mios-sys"]
        for grp in security_groups:
            self.assertIn(f"m mios {grp}", content_50)

class TestRootExtractOwner(unittest.TestCase):
    """A tar extract that runs as root never keeps the archive's numeric owner (uid 1000) on a system path."""

    ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    # The Bibata cursor extracts into /usr/share/icons; upstream ships its files as uid 1000.
    SITES = {"automation/57-gnome.sh": 2, "usr/libexec/mios/mios-cursor-ensure": 1}

    def extracts(self, rel):
        import re
        import shlex
        with open(os.path.join(self.ROOT, rel), encoding="utf-8") as fh:
            for line in fh:
                code = line.split(" #", 1)[0]
                if code.lstrip().startswith("#"):
                    continue
                for match in re.finditer(r"\btar\s+([^;|&]*)", code):
                    words = shlex.split(match.group(1))
                    if "--extract" in words or any(re.fullmatch(r"-[A-Za-z]*x[A-Za-z]*", w) for w in words):
                        yield words

    def test_root_extracts_drop_the_archive_owner(self):
        for rel, count in self.SITES.items():
            commands = list(self.extracts(rel))
            self.assertEqual(len(commands), count, f"{rel}: expected {count} tar extracts, found {commands}")
            for words in commands:
                self.assertIn("--no-same-owner", words, f"{rel}: tar {' '.join(words)} keeps the archive owner as root")

    @unittest.skipUnless(hasattr(os, "geteuid") and os.geteuid() == 0, "tar keeps the archive owner only when run as root")
    def test_no_same_owner_is_what_drops_uid_1000(self):
        import io
        import subprocess
        import tarfile
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            archive = os.path.join(tmp, "theme.tar")
            with tarfile.open(archive, "w") as tar:
                for name, kind in (("Theme", tarfile.DIRTYPE), ("Theme/cursors", tarfile.DIRTYPE), ("Theme/cursors/left_ptr", tarfile.REGTYPE)):
                    info = tarfile.TarInfo(name)
                    info.type, info.uid, info.gid, info.mode = kind, 1000, 1000, 0o755
                    tar.addfile(info, io.BytesIO(b"") if kind == tarfile.REGTYPE else None)
            for i, (flags, owner) in enumerate((([], 1000), (["--no-same-owner"], 0))):
                dest = os.path.join(tmp, f"dest{i}")
                os.mkdir(dest)
                subprocess.run(["tar", "-xf", archive, "-C", dest, *flags], check=True)
                for sub in ("Theme", "Theme/cursors", "Theme/cursors/left_ptr"):
                    self.assertEqual(os.stat(os.path.join(dest, sub)).st_uid, owner, f"tar {flags}: {sub}")

class TestMiosAccountPrivilege(unittest.TestCase):
    """The mios service account has no root: no sudoers rule and no wheel membership. The SSOT login keeps its own
    grant (00-mios-dev, and the wheel membership 11-user.sh renders for it), and the agent's grant is its own file."""

    ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

    def sudoers_rules(self):
        for rel in (os.path.join("etc", "sudoers.d"), os.path.join("usr", "lib", "sudoers.d")):
            directory = os.path.join(self.ROOT, rel)
            for name in sorted(os.listdir(directory)):
                with open(os.path.join(directory, name), encoding="utf-8") as fh:
                    for number, line in enumerate(fh, 1):
                        text = line.strip()
                        if text and not text.startswith(("#", "Defaults", "Cmnd_Alias")):
                            yield f"{rel}/{name}:{number}", text.split()[0], text

    def sysusers_directives(self):
        directory = os.path.join(self.ROOT, "usr", "lib", "sysusers.d")
        for name in sorted(os.listdir(directory)):
            if name.endswith(".conf"):
                with open(os.path.join(directory, name), encoding="utf-8") as fh:
                    for number, line in enumerate(fh, 1):
                        text = line.strip()
                        if text and not text.startswith("#"):
                            yield f"{name}:{number}", " ".join(text.split())

    def test_mios_has_no_sudoers_rule(self):
        hits = [where for where, who, _ in self.sudoers_rules() if who == "mios"]
        self.assertEqual(hits, [], "the mios service account must not have a sudoers rule")

    def test_mios_is_not_in_wheel(self):
        hits = [where for where, text in self.sysusers_directives() if text.split()[:3] == ["m", "mios", "wheel"]]
        self.assertEqual(hits, [], "the mios service account must not be a wheel member")

    def test_sysusers_directives_are_declared_once(self):
        first, repeats = {}, []
        for where, text in self.sysusers_directives():
            if text in first:
                repeats.append(f"{where} repeats {first[text]}: {text}")
            else:
                first[text] = where
        self.assertEqual(repeats, [])

    def test_login_keeps_its_grant_and_wheel(self):
        self.assertIn(("user", "user ALL=(ALL) NOPASSWD: ALL"), [(who, text) for _, who, text in self.sudoers_rules()])
        with open(os.path.join(self.ROOT, "automation", "11-user.sh"), encoding="utf-8") as fh:
            self.assertIn("m ${C_USER} wheel", fh.read())

    def test_agent_grant_is_its_own_and_unchanged(self):
        with open(os.path.join(self.ROOT, "etc", "sudoers.d", "10-mios-hermes"), encoding="utf-8") as fh:
            lines = [line.strip() for line in fh if line.strip() and not line.strip().startswith("#")]
        self.assertEqual(lines, ["Defaults:mios-hermes !requiretty", "mios-hermes ALL=(ALL) NOPASSWD: ALL",
                                 "Defaults:mios-ai !requiretty", "mios-ai ALL=(ALL) NOPASSWD: ALL"])

if __name__ == "__main__":
    unittest.main()
