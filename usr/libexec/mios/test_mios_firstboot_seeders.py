#!/usr/bin/env python3
# AI-hint: Two-sided verification test suite for firstboot seeder ordering and degradation reporting (task_3434).
# AI-related: usr/lib/systemd/system/mios-ai-firstboot.service, usr/lib/systemd/system/mios-forgejo-runner-firstboot.service, usr/libexec/mios/seed-db-config.py, usr/libexec/mios/mios-ai-firstboot, usr/libexec/mios/mios-forgejo-runner-firstboot.sh
# AI-functions: check_ordering, check_seed_db_config, check_runner, check_ai_firstboot, TestFirstbootSeedersTwoSided
"""Two-sided verification controls for Task task_3434.

WHEN forgejo/pgvector/psycopg are unavailable THE SYSTEM SHALL order firstboot
seeders after them and surface the degradation instead of silently skipping.

Each check_* function RUNS the code under test against a scratch fixture and
returns the violations it observed. The positive controls require an empty
list from the shipped files; every negative control plants the pre-fix defect
in a scratch copy and requires the SAME function to name it. Nothing reads the
host: scripts run with their host paths rewritten into the scratch directory,
an empty PATH where they would otherwise find host tools, ambient MIOS_*
scrubbed, psycopg shadowed by a module that refuses to import, and pgvector
pointed at a port nobody listens on.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import tomllib
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, "..", "..", ".."))
_UNITS = os.path.join(_ROOT, "usr", "lib", "systemd", "system")
_LIB = os.path.join(_ROOT, "usr", "lib", "mios")
_TOML = os.path.join(_ROOT, "usr", "share", "mios", "mios.toml")
_SEED_DB = os.path.join(_HERE, "seed-db-config.py")
_RUNNER = os.path.join(_HERE, "mios-forgejo-runner-firstboot.sh")
_AI_FIRSTBOOT = os.path.join(_HERE, "mios-ai-firstboot")
_ORDERING = {
    "mios-ai-firstboot.service": "mios-pgvector.service",
    "mios-forgejo-runner-firstboot.service": "mios-forge-firstboot.service",
}


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _write(path: str, text: str) -> str:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def _clean_env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("MIOS_") and k != "PYTHONPATH"}
    env.update(extra)
    return env


def _unit_list(unit_text: str, key: str) -> list[str]:
    out: list[str] = []
    in_unit = False
    for line in unit_text.splitlines():
        s = line.strip()
        if s.startswith("["):
            in_unit = s == "[Unit]"
        elif in_unit and s.startswith(key + "="):
            out += s.split("=", 1)[1].split()
    return out


def check_ordering(units: dict[str, str], toml_units: dict) -> list[str]:
    """Each seeder is ordered After= and Wants= its dependency, in unit AND SSOT."""
    errors: list[str] = []
    for unit, dep in _ORDERING.items():
        for key in ("After", "Wants"):
            if dep not in _unit_list(units[unit], key):
                errors.append(f"{unit}: {key}= lacks {dep}")
            if dep not in str(toml_units.get(unit, {}).get("Unit", {}).get(key, "")).split():
                errors.append(f'[units."{unit}".Unit] {key} lacks {dep}')
    return errors


def _no_psycopg(scratch: str) -> str:
    """A module dir whose psycopg refuses to import, as on a host without it."""
    d = os.path.join(scratch, "nopsycopg")
    os.makedirs(d, exist_ok=True)
    _write(os.path.join(d, "psycopg.py"), 'raise ImportError("psycopg shadowed by the task_3434 fixture")\n')
    return d


def check_seed_db_config(script: str, scratch: str) -> list[str]:
    """Without psycopg, seed-db-config must exit 2 and say DEGRADED."""
    r = subprocess.run([sys.executable, script], capture_output=True, text=True, timeout=60,
                       env=_clean_env(PYTHONPATH=_no_psycopg(scratch)))
    out = r.stdout + r.stderr
    errors = []
    if r.returncode != 2:
        errors.append(f"seed-db-config exited {r.returncode} without psycopg, want 2")
    if "DEGRADED: psycopg not installed" not in out:
        errors.append("seed-db-config did not report DEGRADED without psycopg")
    return errors


def _run_runner(script: str, scratch: str, token: str | None, sentinel: bool) -> subprocess.CompletedProcess:
    root = os.path.join(scratch, "runner-root")
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(os.path.join(root, "forge-runner"))
    os.makedirs(os.path.join(root, "bin"))
    token_file = os.path.join(root, "runner-token")
    if token is not None:
        _write(token_file, token)
    if sentinel:
        _write(os.path.join(root, "forge-runner", ".runner"), "{}\n")
    text = (_read(script)
            .replace("/srv/mios/forge-runner", os.path.join(root, "forge-runner"))
            .replace("/etc/mios/forge/runner-token", token_file))
    # An empty PATH: no host miosd, podman or forgejo-runner can be reached.
    return subprocess.run(["/bin/bash", "-c", text], capture_output=True, text=True, timeout=60,
                          env=_clean_env(PATH=os.path.join(root, "bin")))


def check_runner(script: str, scratch: str) -> list[str]:
    """No token -> exit non-zero and say DEGRADED; registered -> exit 0, no-op."""
    errors = []
    for label, token in (("missing token", None), ("empty token", "FORGEJO_RUNNER_REGISTRATION_TOKEN=\n")):
        r = _run_runner(script, scratch, token, sentinel=False)
        if r.returncode == 0:
            errors.append(f"runner-firstboot exited 0 with {label}")
        if "DEGRADED" not in r.stderr:
            errors.append(f"runner-firstboot did not report DEGRADED with {label}")
    r = _run_runner(script, scratch, None, sentinel=True)
    if r.returncode != 0 or "nothing to do" not in r.stdout:
        errors.append(f"runner-firstboot did not no-op on an existing sentinel (exit {r.returncode})")
    return errors


def _closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _ai_fixture(script: str, scratch: str, db_block: bool) -> str:
    """The DB-seed block (optional) + the sentinel decision of mios-ai-firstboot."""
    text = _read(script)
    seed = text[text.index("_db_seed_ok=1\n"):text.index('FBLIST="')]
    decide = text[text.index("_db_ok=1\n"):]
    seed = (seed.replace('"$(cd "$(dirname "$0")" && pwd)/../../lib/mios"', '"' + _LIB + '"')
                .replace("/usr/libexec/mios/seed-db-config.py", _SEED_DB))
    venv = os.path.join(scratch, "venv")
    os.makedirs(os.path.join(venv, "bin"), exist_ok=True)
    hermes = _write(os.path.join(venv, "bin", "hermes"), "#!/bin/sh\n")
    os.chmod(hermes, 0o755)
    head = "log(){ echo \"[mios-ai-firstboot] $*\"; }\n"
    if not db_block:
        seed = "_db_seed_ok=1\n_db_config_ok=1\n"
    body = (head + seed + f'VENV="{venv}"\n_ggufs_ok=1\n_vllm_ok=1\n'
            + decide.replace("/var/lib/mios/.ai-firstboot-done", os.path.join(scratch, ".ai-firstboot-done")))
    return body


def check_ai_firstboot(script: str, scratch: str) -> list[str]:
    """pgvector down -> DEGRADED, exit 0 (degrade open), and NO sentinel."""
    errors = []
    sentinel = os.path.join(scratch, ".ai-firstboot-done")
    env = _clean_env(MIOS_PORTS_PGVECTOR=str(_closed_port()), MIOS_PG_WAIT_RETRIES="1",
                     PYTHONPATH=_no_psycopg(scratch))

    # Fixture sanity: with the DB healthy the same decision block DOES write it.
    if os.path.exists(sentinel):
        os.remove(sentinel)
    r = subprocess.run(["bash", "-c", _ai_fixture(script, scratch, db_block=False)],
                       capture_output=True, text=True, timeout=60, env=env)
    if not os.path.exists(sentinel):
        errors.append(f"fixture vacuous: a healthy run wrote no sentinel (exit {r.returncode}) {r.stdout}")
        return errors
    os.remove(sentinel)

    r = subprocess.run(["bash", "-c", _ai_fixture(script, scratch, db_block=True)],
                       capture_output=True, text=True, timeout=120, env=env)
    out = r.stdout + r.stderr
    if r.returncode != 0:
        errors.append(f"ai-firstboot exited {r.returncode} with pgvector down; it must degrade open")
    if "DEGRADED: pgvector port not ready" not in out:
        errors.append("ai-firstboot did not report pgvector DEGRADED")
    if "db=skipped/degraded" not in out:
        errors.append("ai-firstboot's summary did not name the degraded database")
    if os.path.exists(sentinel):
        errors.append("ai-firstboot wrote its done-sentinel although database seeding was skipped")
    return errors


def _digest(paths: list[str]) -> str:
    h = hashlib.sha256()
    for p in paths:
        with open(p, "rb") as f:
            h.update(p.encode() + b"\0" + f.read())
    return h.hexdigest()


class TestFirstbootSeedersTwoSided(unittest.TestCase):
    def setUp(self) -> None:
        self.unit_paths = {u: os.path.join(_UNITS, u) for u in _ORDERING}
        self.watched = list(self.unit_paths.values()) + [_TOML, _SEED_DB, _RUNNER, _AI_FIRSTBOOT]
        self.before = _digest(self.watched)
        with open(_TOML, "rb") as f:
            self.toml_units = tomllib.load(f)["units"]
        self.units = {u: _read(p) for u, p in self.unit_paths.items()}
        self.scratch = tempfile.mkdtemp(prefix="t1141_")

    def tearDown(self) -> None:
        shutil.rmtree(self.scratch, ignore_errors=True)
        self.assertEqual(_digest(self.watched), self.before, "real tree mutated during test")

    def _plant(self, src: str, old: str, new: str, count: int = -1) -> str:
        text = _read(src)
        self.assertIn(old, text, f"plant anchor missing from {os.path.basename(src)}")
        path = _write(os.path.join(self.scratch, "planted-" + os.path.basename(src)), text.replace(old, new, count))
        os.chmod(path, 0o755)
        return path

    # -- positive controls: the shipped files --------------------------------
    def test_positive_ordering(self) -> None:
        self.assertEqual(check_ordering(self.units, self.toml_units), [])

    def test_positive_seed_db_config(self) -> None:
        self.assertEqual(check_seed_db_config(_SEED_DB, self.scratch), [])

    def test_positive_runner(self) -> None:
        self.assertEqual(check_runner(_RUNNER, self.scratch), [])

    def test_positive_ai_firstboot(self) -> None:
        self.assertEqual(check_ai_firstboot(_AI_FIRSTBOOT, self.scratch), [])

    # -- negative controls: each pre-fix defect, named by the same check -----
    def test_negative_ordering(self) -> None:
        units = dict(self.units)
        units["mios-ai-firstboot.service"] = re.sub(r"\s*mios-pgvector\.service", "", units["mios-ai-firstboot.service"])
        self.assertEqual(check_ordering(units, self.toml_units),
                         ["mios-ai-firstboot.service: After= lacks mios-pgvector.service",
                          "mios-ai-firstboot.service: Wants= lacks mios-pgvector.service"])

    def test_negative_seed_db_config_silent(self) -> None:
        planted = self._plant(_SEED_DB, "return 2", "return 0")
        self.assertIn("seed-db-config exited 0 without psycopg, want 2",
                      check_seed_db_config(planted, self.scratch))

    def test_negative_runner_silent(self) -> None:
        text = _read(_RUNNER)
        planted_text, n = re.subn(r"(DEGRADED[^\n]*\n\s*)exit 1", r"\1exit 0", text)
        self.assertEqual(n, 2, "plant must silence both DEGRADED branches")
        planted = _write(os.path.join(self.scratch, "planted-runner.sh"), planted_text)
        errs = check_runner(planted, self.scratch)
        self.assertIn("runner-firstboot exited 0 with missing token", errs)
        self.assertIn("runner-firstboot exited 0 with empty token", errs)

    def test_negative_ai_firstboot_sentinel_ungated(self) -> None:
        planted = self._plant(_AI_FIRSTBOOT, ' && [ "$_db_ok" -eq 1 ]', "", 1)
        self.assertIn("ai-firstboot wrote its done-sentinel although database seeding was skipped",
                      check_ai_firstboot(planted, self.scratch))

    def test_negative_ai_firstboot_silent_skip(self) -> None:
        # Both DB seeders swallow their failure: the pre-fix "silently skip".
        planted = self._plant(_AI_FIRSTBOOT, "then\n    _db_seed_ok=0", "then\n    _db_seed_ok=1")
        planted = self._plant(planted, "        _db_config_ok=0\n", "        _db_config_ok=1\n")
        errs = check_ai_firstboot(planted, self.scratch)
        self.assertIn("ai-firstboot wrote its done-sentinel although database seeding was skipped", errs)


if __name__ == "__main__":
    unittest.main()
