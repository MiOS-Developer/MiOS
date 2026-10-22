#!/usr/bin/env python3
# AI-hint: Two-sided test for write paths of ProtectSystem=strict units (task_3432); checks the state dirs are writable and that any StateDirectory= owner/mode agrees with tmpfiles.d.
# AI-related: usr/lib/systemd/system/mios-agents.service, usr/lib/systemd/system/mios-cron-director.service, usr/lib/tmpfiles.d/50-mios.conf, usr/share/mios/mios.toml
"""Two-sided verification controls for task_3432.

Every expected value is read from the tree, never written here as a literal:

* the paths a unit writes are the tmpfiles.d-declared directories that its
  Exec* command lines and the scripts they run refer to;
* each such path must be writable under ProtectSystem=strict, through
  ReadWritePaths= or StateDirectory=;
* when a unit claims a path with StateDirectory=, the owner systemd will
  enforce (User=/Group=, root when absent) and StateDirectoryMode= (0755 when
  absent) must equal the owner and mode usr/lib/tmpfiles.d declares for the
  same path. systemd recursively chowns a StateDirectory= to the unit's user
  on every start, so a mismatch re-owns the tmpfiles-declared directory.

Negative controls plant defects in a scratch copy of the files involved and
require a diagnostic naming the unit and path; the real tree is hashed before
and after to show nothing was mutated.

Runtime controls (firstboot across boots, EROFS in the journal) need a booted
systemd host and are not run by this file.
"""
from __future__ import annotations

import glob
import hashlib
import os
import re
import shutil
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.normpath(os.path.join(_HERE, "..", "..", ".."))

UNIT_DIR = os.path.join("usr", "lib", "systemd", "system")
TMPFILES_DIR = os.path.join("usr", "lib", "tmpfiles.d")
SYSUSERS_DIR = os.path.join("usr", "lib", "sysusers.d")
# The units this task covers. Everything checked about them is read from disk.
UNITS = ("mios-agents.service", "mios-cron-director.service")

# systemd.exec / tmpfiles.d documented defaults, used only when a field is absent.
_STATE_DIR_DEFAULT_MODE = "0755"
_TMPFILES_DEFAULT_MODE = "0755"
_ROOT = "root"

_VAR_PATH_RE = re.compile(r"/var/lib/[A-Za-z0-9._/-]+")


def parse_unit(path: str) -> dict[str, dict[str, list[str]]]:
    """Return {section: {key: [values...]}}, joining backslash continuations."""
    sections: dict[str, dict[str, list[str]]] = {}
    current: dict[str, list[str]] | None = None
    logical: list[str] = []
    buf = ""
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.rstrip("\n")
            if buf:
                buf += " " + line.strip()
            else:
                buf = line.strip()
            if buf.endswith("\\"):
                buf = buf[:-1].rstrip()
                continue
            logical.append(buf)
            buf = ""
    if buf:
        logical.append(buf)
    for line in logical:
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = sections.setdefault(line[1:-1], {})
            continue
        if current is not None and "=" in line:
            key, val = line.split("=", 1)
            current.setdefault(key.strip(), []).append(val.strip())
    return sections


def parse_tmpfiles(root: str) -> dict[str, tuple[str, str, str]]:
    """Return {path: (mode, user, group)} for directory lines in tmpfiles.d."""
    decl: dict[str, tuple[str, str, str]] = {}
    for conf in sorted(glob.glob(os.path.join(root, TMPFILES_DIR, "*.conf"))):
        with open(conf, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                f = line.split()
                if len(f) < 2 or f[0].rstrip("!-=+^~") not in ("d", "D", "v", "q", "Q"):
                    continue
                mode = f[2] if len(f) > 2 and f[2] != "-" else _TMPFILES_DEFAULT_MODE
                user = f[3] if len(f) > 3 and f[3] != "-" else _ROOT
                group = f[4] if len(f) > 4 and f[4] != "-" else _ROOT
                decl[f[1].rstrip("/")] = (mode.lstrip("~:"), user, group)
    return decl


def parse_sysusers(root: str) -> dict[str, str]:
    """Return {name: uid} from sysusers.d `u` lines, so names and ids compare."""
    ids: dict[str, str] = {_ROOT: "0"}
    for conf in sorted(glob.glob(os.path.join(root, SYSUSERS_DIR, "*.conf"))):
        with open(conf, encoding="utf-8") as fh:
            for line in fh:
                f = line.split()
                if len(f) >= 3 and f[0] in ("u", "u!", "g") and f[2] != "-":
                    ids.setdefault(f[1], f[2].split(":")[0])
    return ids


def _ident(name: str, ids: dict[str, str]) -> str:
    return ids.get(name, name)


def _mode(text: str) -> int:
    return int(text, 8)


def _exec_text(root: str, svc: dict[str, list[str]]) -> str:
    """Exec* command lines plus the content of each executable they run."""
    parts: list[str] = []
    for key in ("ExecStartPre", "ExecStart", "ExecStartPost", "ExecReload", "ExecStop"):
        for cmd in svc.get(key, []):
            parts.append(cmd)
            exe = cmd.split()[0].lstrip("-@:+!") if cmd.split() else ""
            local = os.path.join(root, exe.lstrip("/"))
            if exe.startswith("/") and os.path.isfile(local):
                try:
                    with open(local, encoding="utf-8") as fh:
                        parts.append(fh.read())
                except UnicodeDecodeError:
                    pass
    return "\n".join(parts)


def written_state_dirs(root: str, svc: dict[str, list[str]],
                       tmp: dict[str, tuple[str, str, str]]) -> set[str]:
    """tmpfiles-declared dirs the unit's commands/scripts refer to (longest prefix)."""
    found: set[str] = set()
    for ref in _VAR_PATH_RE.findall(_exec_text(root, svc)):
        ref = ref.rstrip("/.")
        best = ""
        for decl in tmp:
            if (ref == decl or ref.startswith(decl + "/")) and len(decl) > len(best):
                best = decl
        if best:
            # The most specific declaration is the one whose owner/mode
            # governs the path; a grant on an ancestor still covers it.
            found.add(best)
    return found


def _state_dirs(svc: dict[str, list[str]]) -> list[str]:
    out: list[str] = []
    for val in svc.get("StateDirectory", []):
        for entry in val.split():
            out.append("/var/lib/" + entry.split(":")[0].strip("/"))
    return out


def _covered(path: str, grants: list[str]) -> bool:
    return any(path == g or path.startswith(g.rstrip("/") + "/") for g in grants)


def verify_units(root: str, units: tuple[str, ...] = UNITS) -> tuple[list[str], dict[str, set[str]]]:
    """Return (errors, {unit: written dirs}) for the given units under root."""
    errors: list[str] = []
    coverage: dict[str, set[str]] = {}
    tmp = parse_tmpfiles(root)
    ids = parse_sysusers(root)
    for unit in units:
        path = os.path.join(root, UNIT_DIR, unit)
        if not os.path.isfile(path):
            errors.append(f"{unit}: unit file missing at {path}")
            continue
        svc = parse_unit(path).get("Service", {})
        if svc.get("ProtectSystem", [""])[-1] != "strict":
            errors.append(f"{unit}: ProtectSystem=strict is not set")
        rw = [p.lstrip("-") for v in svc.get("ReadWritePaths", []) for p in v.split()]
        state = _state_dirs(svc)
        written = written_state_dirs(root, svc, tmp)
        coverage[unit] = written

        # 1. Every tmpfiles-declared dir the unit writes must be writable.
        for d in sorted(written):
            if not _covered(d, rw + state):
                errors.append(f"{unit}: writes {d} but neither ReadWritePaths= nor StateDirectory= covers it")

        # 2. StateDirectory= owner/mode must agree with tmpfiles.d.
        user = svc.get("User", [_ROOT])[-1]
        group = svc.get("Group", [user])[-1]
        mode = svc.get("StateDirectoryMode", [_STATE_DIR_DEFAULT_MODE])[-1]
        for d in state:
            if d not in tmp:
                continue
            t_mode, t_user, t_group = tmp[d]
            if _ident(user, ids) != _ident(t_user, ids):
                errors.append(f"{unit}: StateDirectory {d} would be chowned to user {user}, "
                              f"tmpfiles.d declares {t_user}")
            if _ident(group, ids) != _ident(t_group, ids):
                errors.append(f"{unit}: StateDirectory {d} would be chgrp'd to {group}, "
                              f"tmpfiles.d declares {t_group}")
            if _mode(mode) != _mode(t_mode):
                errors.append(f"{unit}: StateDirectory {d} mode {mode} != tmpfiles.d mode {t_mode}")
            if d in rw:
                errors.append(f"{unit}: ReadWritePaths= repeats StateDirectory path {d}")
    return errors, coverage


def _tree_digest(root: str) -> str:
    h = hashlib.sha256()
    for rel in [os.path.join(UNIT_DIR, u) for u in UNITS]:
        with open(os.path.join(root, rel), "rb") as fh:
            h.update(rel.encode() + b"\0" + fh.read())
    for conf in sorted(glob.glob(os.path.join(root, TMPFILES_DIR, "*.conf"))):
        with open(conf, "rb") as fh:
            h.update(conf.encode() + b"\0" + fh.read())
    return h.hexdigest()


class TestUnitHardening(unittest.TestCase):
    def setUp(self) -> None:
        self.root = os.path.abspath(os.environ.get("MIOS_ROOT", _REPO_ROOT))
        self.digest = _tree_digest(self.root)

    def tearDown(self) -> None:
        self.assertEqual(_tree_digest(self.root), self.digest,
                         "real tree mutated during test")

    # --- positive control -------------------------------------------------
    def test_positive_control(self) -> None:
        errors, coverage = verify_units(self.root)
        self.assertEqual(errors, [])
        # Non-vacuous: each unit must be found to write at least one
        # tmpfiles-declared dir, else the writability check checked nothing.
        for unit in UNITS:
            self.assertTrue(coverage.get(unit), f"{unit}: no tmpfiles-declared write path found")

    # --- negative controls ------------------------------------------------
    def _scratch(self, tmpdir: str) -> str:
        for rel in [os.path.join(UNIT_DIR, u) for u in UNITS]:
            os.makedirs(os.path.join(tmpdir, os.path.dirname(rel)), exist_ok=True)
            shutil.copy2(os.path.join(self.root, rel), os.path.join(tmpdir, rel))
            svc = parse_unit(os.path.join(self.root, rel)).get("Service", {})
            for key in ("ExecStartPre", "ExecStart"):
                for cmd in svc.get(key, []):
                    exe = cmd.split()[0].lstrip("-@:+!")
                    src = os.path.join(self.root, exe.lstrip("/"))
                    if exe.startswith("/") and os.path.isfile(src):
                        dst = os.path.join(tmpdir, exe.lstrip("/"))
                        os.makedirs(os.path.dirname(dst), exist_ok=True)
                        shutil.copy2(src, dst)
        for sub in (TMPFILES_DIR, SYSUSERS_DIR):
            src = os.path.join(self.root, sub)
            if os.path.isdir(src):
                shutil.copytree(src, os.path.join(tmpdir, sub))
        return tmpdir

    @staticmethod
    def _edit(path: str, pattern: str, repl: str) -> None:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        new, n = re.subn(pattern, repl, text, flags=re.MULTILINE)
        if n == 0:
            raise AssertionError(f"plant matched nothing in {path}: {pattern!r}")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(new)

    def _tmp_decl(self, scratch: str, unit: str) -> tuple[str, tuple[str, str, str]]:
        svc = parse_unit(os.path.join(scratch, UNIT_DIR, unit)).get("Service", {})
        tmp = parse_tmpfiles(scratch)
        dirs = sorted(written_state_dirs(scratch, svc, tmp), key=len)
        self.assertTrue(dirs, f"{unit}: no write path to plant against")
        return dirs[0], tmp[dirs[0]]

    def test_negative_missing_rw_grant(self) -> None:
        for unit in UNITS:
            with tempfile.TemporaryDirectory(prefix="t1139_rw_") as tmpdir:
                scratch = self._scratch(tmpdir)
                d, _ = self._tmp_decl(scratch, unit)
                self._edit(os.path.join(scratch, UNIT_DIR, unit), r"^ReadWritePaths=.*$", "")
                errors, _ = verify_units(scratch)
                self.assertIn(f"{unit}: writes {d} but neither ReadWritePaths= nor "
                              f"StateDirectory= covers it", errors)

    def test_negative_statedirectory_owner_mismatch(self) -> None:
        # The shipped defect: a root-running unit claiming a dir tmpfiles gives
        # to another owner (mios-agents: StateDirectory=mios/agents, no User=).
        unit = "mios-agents.service"
        with tempfile.TemporaryDirectory(prefix="t1139_own_") as tmpdir:
            scratch = self._scratch(tmpdir)
            d, (t_mode, t_user, _) = self._tmp_decl(scratch, unit)
            path = os.path.join(scratch, UNIT_DIR, unit)
            sd = d[len("/var/lib/"):]
            self._edit(path, r"^ReadWritePaths=", f"StateDirectory={sd}\nReadWritePaths=")
            errors, _ = verify_units(scratch)
            svc = parse_unit(path).get("Service", {})
            user = svc.get("User", [_ROOT])[-1]
            self.assertNotEqual(_ident(user, parse_sysusers(scratch)),
                                _ident(t_user, parse_sysusers(scratch)),
                                "plant precondition: unit user equals tmpfiles owner")
            self.assertIn(f"{unit}: StateDirectory {d} would be chowned to user {user}, "
                          f"tmpfiles.d declares {t_user}", errors)
            self.assertIn(f"{unit}: ReadWritePaths= repeats StateDirectory path {d}", errors)

    def test_negative_statedirectory_mode_mismatch(self) -> None:
        # The shipped defect: cron-director StateDirectoryMode=0770 vs tmpfiles 2770.
        unit = "mios-cron-director.service"
        with tempfile.TemporaryDirectory(prefix="t1139_mode_") as tmpdir:
            scratch = self._scratch(tmpdir)
            d, (t_mode, t_user, t_group) = self._tmp_decl(scratch, unit)
            bad = format(_mode(t_mode) & 0o777, "04o")  # drop setgid/sticky bits
            self.assertNotEqual(_mode(bad), _mode(t_mode), "plant precondition: no special bits to drop")
            path = os.path.join(scratch, UNIT_DIR, unit)
            sd = d[len("/var/lib/"):]
            self._edit(path, r"^ReadWritePaths=.*$",
                       f"StateDirectory={sd}\nStateDirectoryMode={bad}")
            errors, _ = verify_units(scratch)
            self.assertIn(f"{unit}: StateDirectory {d} mode {bad} != tmpfiles.d mode {t_mode}", errors)
            # Owner matches (User=/Group= equal tmpfiles), so only the mode is named.
            self.assertEqual([e for e in errors if "chown" in e or "chgrp" in e], [])

    def test_matching_statedirectory_is_accepted(self) -> None:
        # Guards against a comparator that flags any StateDirectory= at all:
        # a StateDirectory= whose mode equals tmpfiles must pass.
        unit = "mios-cron-director.service"
        with tempfile.TemporaryDirectory(prefix="t1139_ok_") as tmpdir:
            scratch = self._scratch(tmpdir)
            d, (t_mode, _, _) = self._tmp_decl(scratch, unit)
            sd = d[len("/var/lib/"):]
            self._edit(os.path.join(scratch, UNIT_DIR, unit), r"^ReadWritePaths=.*$",
                       f"StateDirectory={sd}\nStateDirectoryMode={t_mode}")
            errors, _ = verify_units(scratch)
            self.assertEqual(errors, [])

    def test_negative_tmpfiles_drift(self) -> None:
        # The comparison reads tmpfiles, not a literal: change the declared
        # mode there and a previously matching StateDirectory= must fail.
        unit = "mios-cron-director.service"
        with tempfile.TemporaryDirectory(prefix="t1139_tmp_") as tmpdir:
            scratch = self._scratch(tmpdir)
            d, (t_mode, _, _) = self._tmp_decl(scratch, unit)
            sd = d[len("/var/lib/"):]
            self._edit(os.path.join(scratch, UNIT_DIR, unit), r"^ReadWritePaths=.*$",
                       f"StateDirectory={sd}\nStateDirectoryMode={t_mode}")
            new_mode = "0700" if _mode(t_mode) != 0o700 else "0750"
            for conf in glob.glob(os.path.join(scratch, TMPFILES_DIR, "*.conf")):
                with open(conf, encoding="utf-8") as fh:
                    text = fh.read()
                text2 = re.sub(rf"^(d\s+{re.escape(d)}\s+)\S+", rf"\g<1>{new_mode}", text, flags=re.MULTILINE)
                if text2 != text:
                    with open(conf, "w", encoding="utf-8") as fh:
                        fh.write(text2)
            errors, _ = verify_units(scratch)
            self.assertIn(f"{unit}: StateDirectory {d} mode {t_mode} != tmpfiles.d mode {new_mode}", errors)

    def test_negative_protectsystem_dropped(self) -> None:
        for unit in UNITS:
            with tempfile.TemporaryDirectory(prefix="t1139_ps_") as tmpdir:
                scratch = self._scratch(tmpdir)
                self._edit(os.path.join(scratch, UNIT_DIR, unit), r"^ProtectSystem=.*$", "")
                errors, _ = verify_units(scratch)
                self.assertIn(f"{unit}: ProtectSystem=strict is not set", errors)


if __name__ == "__main__":
    sys.exit(unittest.main())
