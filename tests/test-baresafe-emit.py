#!/usr/bin/env python3
# AI-hint: Law 10 BARE-SAFE-ENV unit test. Drives system-sync-env.sh emit() directly, one case per unsafe character, so the REJECT path is exercised where it can actually fail.
# AI-related: usr/libexec/mios/system-sync-env.sh, automation/99-postcheck.sh, usr/share/mios/mios.toml
# AI-doc: usr/share/doc/mios/manual/tests.md
"""Per-character tests sited on emit(), the producer -- not on its output.

A render-based test cannot fail here: emit() strips those characters upstream,
so the file is clean whatever emit() did with what it dropped. Each case asserts
rc==1, nothing on stdout, and the key named on stderr.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

ROOT = os.environ.get("MIOS_DRIFT_ROOT") or os.environ.get("MIOS_ROOT") or os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
PRODUCER = os.path.join(ROOT, "usr", "libexec", "mios", "system-sync-env.sh")

# One case per character class named in Law 10: "no quotes/whitespace/$/backtick/#".
UNSAFE_CASES = (
    ("space", "two words"),
    ("tab", "two\twords"),
    ("double quote", 'say"what'),
    ("single quote", "say'what"),
    ("dollar", "http://localhost:${MIOS_PORTS_AGENT_PIPE}/v1"),
    ("backtick", "now`date`"),
    ("hash", "value#comment"),
)

PROBE = "MIOS_PROBE_BARESAFE"
DECLARED = "MIOS_PROBE_DECLARED"

_failures: list[str] = []


def fail(msg: str) -> None:
    _failures.append(msg)
    print("FAIL: " + msg, file=sys.stderr)


def producer_source() -> tuple[str, str]:
    """Lift the live _ENV_UNSAFE pattern and emit() body from the script.

    Both matches are hard requirements: an empty harness would return 0.
    """
    with open(PRODUCER, encoding="utf-8") as fh:
        src = fh.read()
    pat = re.search(r"^_ENV_UNSAFE=.*$", src, re.M)
    body = re.search(r"^emit\(\)\s*\{$.*?^\}$", src, re.M | re.S)
    if not pat or not body:
        print("FAIL: could not lift _ENV_UNSAFE and/or emit() out of %s -- this "
              "test cannot assert on a function it did not find" % PRODUCER, file=sys.stderr)
        sys.exit(2)
    if "_ENV_UNSAFE" not in body.group(0):
        print("FAIL: emit() no longer consults _ENV_UNSAFE -- the bare-safe filter "
              "moved, and this test is aimed at the wrong code", file=sys.stderr)
        sys.exit(2)
    return pat.group(0), body.group(0)


def run_emit(unsafe_line: str, emit_src: str, key: str, value: str):
    """Call the real emit() with one key/value pair.

    The value travels in the environment so a backtick is never re-expanded here.
    """
    harness = "\n".join((
        "set -u",
        unsafe_line,
        '_NON_BARE_OK=",%s,"' % DECLARED,
        emit_src,
        'emit "$_PROBE_KEY" "$_PROBE_VALUE"',
    ))
    env = dict(os.environ, _PROBE_KEY=key, _PROBE_VALUE=value)
    return subprocess.run(["bash", "-c", harness], capture_output=True, text=True, env=env)


def main() -> int:
    unsafe_line, emit_src = producer_source()

    # Control: a bare value must still be emitted verbatim. Without it every
    # assertion below could be satisfied by an emit() that rejects everything.
    got = run_emit(unsafe_line, emit_src, PROBE, "http://localhost:8700/v1")
    if got.returncode != 0 or got.stdout.strip() != "%s=http://localhost:8700/v1" % PROBE:
        fail("a BARE value did not survive emit(): rc=%d stdout=%r stderr=%r"
             % (got.returncode, got.stdout, got.stderr))

    for label, value in UNSAFE_CASES:
        got = run_emit(unsafe_line, emit_src, PROBE, value)
        if got.returncode == 0:
            fail("%s SILENTLY DROPPED: a value carrying a %s (%r) made emit() return "
                 "%d, so the key never reaches install.env and the producer reports "
                 "success. Law 10 requires an undeclared unsafe value to FAIL."
                 % (PROBE, label, value, got.returncode))
        if PROBE in got.stdout:
            fail("%s: a value carrying a %s was written to install.env anyway: %r"
                 % (PROBE, label, got.stdout))
        if PROBE not in got.stderr:
            fail("%s: a value carrying a %s was rejected without naming the key on "
                 "stderr (%r) -- an unnamed rejection is a silent one"
                 % (PROBE, label, got.stderr))

    # The declared escape hatch: [security.non_bare_env] turns the same value
    # into a reported skip. If this stopped working the fix above would be
    # unusable and the pressure would go back onto loosening the filter.
    got = run_emit(unsafe_line, emit_src, DECLARED, "Firstname Lastname")
    if got.returncode != 0:
        fail("%s is declared in [security.non_bare_env] yet emit() returned %d"
             % (DECLARED, got.returncode))
    if DECLARED in got.stdout:
        fail("%s is declared non-bare but its value was written to install.env: %r"
             % (DECLARED, got.stdout))
    if DECLARED not in got.stderr:
        fail("%s was skipped without saying so on stderr (%r)" % (DECLARED, got.stderr))

    # An undeclared key must NOT be waved through by a prefix/substring match on
    # the allowlist; `,KEY,` is the guard and this proves it is doing work.
    got = run_emit(unsafe_line, emit_src, DECLARED + "_SUFFIX", "two words")
    if got.returncode == 0:
        fail("%s_SUFFIX is not declared, yet emit() accepted it -- the "
             "[security.non_bare_env] match is a substring match" % DECLARED)

    if _failures:
        print("test-baresafe-emit: %d failure(s)" % len(_failures), file=sys.stderr)
        return 1
    print("test-baresafe-emit: PASS -- emit() rejects all %d unsafe character "
          "classes with rc=1, emits nothing, and names the key" % len(UNSAFE_CASES))
    return 0


if __name__ == "__main__":
    sys.exit(main())
