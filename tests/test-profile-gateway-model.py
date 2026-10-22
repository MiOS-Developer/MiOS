#!/usr/bin/env python3
# AI-hint: Regression test verifying that 50-mios.sh exports MIOS_AI_GATEWAY_MODEL from MIOS_AI_AGENT_MODEL or unsets it.
# AI-related: etc/profile.d/50-mios.sh, usr/share/mios/ai/system.md, usr/share/mios/mios.toml
# AI-doc: usr/share/doc/mios/manual/tests.md
"""Verify etc/profile.d/50-mios.sh exports MIOS_AI_GATEWAY_MODEL from MIOS_AI_AGENT_MODEL,
and leaves it unset (not empty) when unknown."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.environ.get("MIOS_DRIFT_ROOT") or os.environ.get("MIOS_ROOT") or "/"
_PROFILE_SCRIPT = os.path.join(_ROOT, "etc/profile.d/50-mios.sh")
if not os.path.isfile(_PROFILE_SCRIPT):
    candidate = os.path.normpath(os.path.join(_HERE, "..", "etc/profile.d/50-mios.sh"))
    if os.path.isfile(candidate):
        _PROFILE_SCRIPT = candidate
    else:
        _PROFILE_SCRIPT = "/etc/profile.d/50-mios.sh"


def run_profile_test(
    env_overrides: dict[str, str],
    probe_cmd: str,
    script_content: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Source 50-mios.sh inside an interactive bash subshell with isolated host env files."""
    if script_content is None:
        with open(_PROFILE_SCRIPT, "r", encoding="utf-8") as fh:
            script_content = fh.read()
    with tempfile.TemporaryDirectory(prefix="mios-profile-gw-") as tmpdir:
        isolated = (
            script_content.replace("/etc/mios", os.path.join(tmpdir, "etc-mios"))
            .replace("/usr/lib/mios/userenv.sh", os.path.join(tmpdir, "no-userenv.sh"))
            .replace("/usr/share/mios/tools/lib/userenv.sh", os.path.join(tmpdir, "no-userenv2.sh"))
        )
        script_path = os.path.join(tmpdir, "50-mios.sh")
        with open(script_path, "w", encoding="utf-8") as fh:
            fh.write(isolated)
        base_env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": tmpdir,
            "MIOS_SKIP_MOTD": "1",
            "MIOS_THEME_PROJECTED": "1",
        }
        base_env.update(env_overrides)
        full_cmd = f'. "{script_path}" >/dev/null 2>&1\n{probe_cmd}'
        return subprocess.run(
            ["bash", "--norc", "--noprofile", "-i", "-c", full_cmd],
            env=base_env,
            capture_output=True,
            text=True,
        )


def test_positive_control_model_fallback() -> None:
    assert os.path.isfile(_PROFILE_SCRIPT), f"Missing {_PROFILE_SCRIPT}"

    with open(_PROFILE_SCRIPT, "r", encoding="utf-8") as f:
        content = f.read()
    assert '_mios_gm="${MIOS_AI_GATEWAY_MODEL:-${MIOS_AI_AGENT_MODEL:-}}"' in content, (
        "Missing gateway model fallback assignment in 50-mios.sh"
    )
    assert 'if [ -n "$_mios_gm" ]; then export MIOS_AI_GATEWAY_MODEL="$_mios_gm"; else unset MIOS_AI_GATEWAY_MODEL; fi' in content, (
        "Missing unset branch for MIOS_AI_GATEWAY_MODEL in 50-mios.sh"
    )

    probe_fallback = (
        'if [ "${MIOS_AI_GATEWAY_MODEL:-}" = "agent-model-8b" ]; then '
        'echo "MATCH"; exit 0; else echo "MISMATCH: ${MIOS_AI_GATEWAY_MODEL:-}"; exit 1; fi'
    )
    res = run_profile_test({"MIOS_AI_AGENT_MODEL": "agent-model-8b"}, probe_fallback)
    assert res.returncode == 0, f"Fallback failed: {res.stdout} {res.stderr}"

    probe_override = (
        'if [ "${MIOS_AI_GATEWAY_MODEL:-}" = "override-gw" ]; then '
        'echo "MATCH"; exit 0; else exit 1; fi'
    )
    res_override = run_profile_test(
        {"MIOS_AI_GATEWAY_MODEL": "override-gw", "MIOS_AI_AGENT_MODEL": "agent-model-8b"},
        probe_override,
    )
    assert res_override.returncode == 0, "Explicit gateway model was clobbered by agent model"

    probe_unset = (
        'if [ -v MIOS_AI_GATEWAY_MODEL ]; then '
        'echo "FAILED: variable is set to $MIOS_AI_GATEWAY_MODEL"; exit 1; else echo "UNSET_OK"; exit 0; fi'
    )
    res_unset = run_profile_test({}, probe_unset)
    assert res_unset.returncode == 0, f"Unset assertion failed: {res_unset.stdout} {res_unset.stderr}"


def test_negative_control_empty_string_rejection() -> None:
    with open(_PROFILE_SCRIPT, "r", encoding="utf-8") as f:
        content = f.read()

    broken_empty = content.replace(
        'if [ -n "$_mios_gm" ]; then export MIOS_AI_GATEWAY_MODEL="$_mios_gm"; else unset MIOS_AI_GATEWAY_MODEL; fi',
        'export MIOS_AI_GATEWAY_MODEL="$_mios_gm"',
    )
    probe_unset = 'if [ -v MIOS_AI_GATEWAY_MODEL ]; then exit 1; else exit 0; fi'
    res = run_profile_test({}, probe_unset, script_content=broken_empty)
    assert res.returncode != 0, "Negative control failed: empty string export was not rejected"

    broken_fallback = content.replace(
        '_mios_gm="${MIOS_AI_GATEWAY_MODEL:-${MIOS_AI_AGENT_MODEL:-}}"',
        '_mios_gm="${MIOS_AI_GATEWAY_MODEL:-}"',
    )
    probe_fallback = 'if [ "${MIOS_AI_GATEWAY_MODEL:-}" = "agent-model-8b" ]; then exit 0; else exit 1; fi'
    res_broken = run_profile_test(
        {"MIOS_AI_AGENT_MODEL": "agent-model-8b"},
        probe_fallback,
        script_content=broken_fallback,
    )
    assert res_broken.returncode != 0, "Negative control failed: missing fallback was not detected"


def main() -> int:
    print("[test-profile-gateway-model] Running positive controls (model fallback and unset check)...")
    test_positive_control_model_fallback()
    print("[test-profile-gateway-model] Running negative controls...")
    test_negative_control_empty_string_rejection()
    print("[test-profile-gateway-model] PASS: Gateway model profile rules verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
