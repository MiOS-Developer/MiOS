#!/usr/bin/env python3
# AI-hint: Regression test verifying that tmux pane scope drop-in sets OOMPolicy=continue.
# AI-related: usr/lib/systemd/user/tmux-spawn-.scope.d/10-mios-oom.conf, usr/share/mios/mios.toml
# AI-doc: usr/share/doc/mios/manual/tests.md
"""Verify usr/lib/systemd/user/tmux-spawn-.scope.d/10-mios-oom.conf sets OOMPolicy=continue."""

from __future__ import annotations

import configparser
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.environ.get("MIOS_DRIFT_ROOT") or os.environ.get("MIOS_ROOT") or "/"
_CONF_PATH = os.path.join(_ROOT, "usr/lib/systemd/user/tmux-spawn-.scope.d/10-mios-oom.conf")
if not os.path.isfile(_CONF_PATH):
    candidate = os.path.normpath(os.path.join(_HERE, "..", "usr/lib/systemd/user/tmux-spawn-.scope.d/10-mios-oom.conf"))
    if os.path.isfile(candidate):
        _CONF_PATH = candidate
    else:
        _CONF_PATH = "/usr/lib/systemd/user/tmux-spawn-.scope.d/10-mios-oom.conf"



def parse_oom_conf(filepath: str) -> str:
    if not os.path.isfile(filepath):
        raise FileNotFoundError(f"Missing config file: {filepath}")
    parser = configparser.ConfigParser()
    parser.read(filepath)
    if not parser.has_section("Scope"):
        raise ValueError(f"Missing [Scope] section in {filepath}")
    if not parser.has_option("Scope", "OOMPolicy"):
        raise ValueError(f"Missing OOMPolicy option in [Scope] section of {filepath}")
    return parser.get("Scope", "OOMPolicy")


def test_positive_control() -> None:
    assert os.path.isfile(_CONF_PATH), f"Missing drop-in file: {_CONF_PATH}"
    policy = parse_oom_conf(_CONF_PATH)
    assert policy == "continue", f"Expected OOMPolicy=continue, found '{policy}' in {_CONF_PATH}"


def test_negative_controls() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        # Negative 1: OOMPolicy=stop
        f1 = os.path.join(tmpdir, "bad1.conf")
        with open(f1, "w", encoding="utf-8") as f:
            f.write("[Scope]\nOOMPolicy=stop\n")
        assert parse_oom_conf(f1) != "continue", "Negative control failed: accepted OOMPolicy=stop"

        # Negative 2: Missing [Scope] section
        f2 = os.path.join(tmpdir, "bad2.conf")
        with open(f2, "w", encoding="utf-8") as f:
            f.write("[Service]\nOOMPolicy=continue\n")
        try:
            parse_oom_conf(f2)
            raise AssertionError("Negative control failed: accepted missing [Scope]")
        except ValueError:
            pass

        # Negative 3: Missing OOMPolicy directive
        f3 = os.path.join(tmpdir, "bad3.conf")
        with open(f3, "w", encoding="utf-8") as f:
            f.write("[Scope]\nMemoryMax=8G\n")
        try:
            parse_oom_conf(f3)
            raise AssertionError("Negative control failed: accepted missing OOMPolicy")
        except ValueError:
            pass

        # Negative 4: Missing file
        try:
            parse_oom_conf(os.path.join(tmpdir, "nonexistent.conf"))
            raise AssertionError("Negative control failed: accepted nonexistent file")
        except FileNotFoundError:
            pass


def main() -> int:
    print("[test-tmux-oom-policy] Running positive control on 10-mios-oom.conf...")
    test_positive_control()
    print("[test-tmux-oom-policy] Running negative controls on invalid configurations...")
    test_negative_controls()
    print("[test-tmux-oom-policy] PASS: OOMPolicy=continue verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
