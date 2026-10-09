# AI-hint: Tests for task_910 & task_911: autonomous self-replication build trigger and digest verification.
# AI-functions: test_build_context_ignores_generated_artifacts, test_build_context_preserves_tracked_sources, test_self_replication_build_and_digest

"""Tests for task_910 & task_911: autonomous self-replication build trigger and digest verification."""
import re
import sys
import subprocess
import tempfile
import tomllib
from pathlib import Path
sys.path.insert(0, "usr/libexec/mios/deploy")
from self_replicate import SelfReplicationDaemon

def test_build_context_ignores_generated_artifacts():
    """Nested build output and cache symlinks cannot become OCI payloads."""
    root = Path(__file__).resolve().parents[1]
    rules = set((root / ".containerignore").read_text().splitlines())
    for pattern in ("**/target", "**/target/**", "**/__pycache__", "**/__pycache__/**", "**/*.pyc"):
        assert pattern in rules, f"missing recursive container exclusion: {pattern}"
    assert (root / ".dockerignore").resolve() == root / ".containerignore", "container engines must share one ignore policy"

def test_self_replication_build_and_digest():
    """Verify self-replication builds OCI image and produces valid sha256 digest."""
    daemon = SelfReplicationDaemon()
    commit_sha = "41146deb8a7b9c1d2e3f4a5b6c7d8e9f01234567"

    res = daemon.trigger_self_build(commit_sha, dry_run=True)
    assert res.git_commit_sha == commit_sha
    assert res.staged_for_switch
    assert daemon.verify_image_signature(res)
    assert len(daemon.build_history) == 1

def test_build_context_preserves_tracked_sources():
    """Image gates see canonical artifact paths and omitted tracked consumers."""
    root = Path(__file__).resolve().parents[1]
    recipe = (root / "Containerfile").read_text()
    assert "/ctx/config/artifacts/" in recipe, "artifact recipes must retain their source path"
    assert "/ctx/bib-configs" not in recipe, "renamed artifacts invalidate source gates"
    restore = "git -C /tmp/build ls-files --deleted -z | git -C /tmp/build checkout-index -z --stdin"
    assert restore in recipe, "partial image context must restore omitted tracked consumers"
    assert recipe.index(restore) < recipe.index("miosd drift-check"), "restore sources before evaluating drift"
    runner = (root / "automation/build.sh").read_text()
    completed = re.search(r'case " \$_name " in\n.*?\n    esac', runner, re.S)
    assert completed, "the phase runner must identify stages already completed by Containerfile"
    probe = 'for _name in 55-native-build.sh new-phase.sh; do\n' + completed.group() + '\nprintf "%s\\n" "$_name"\ndone\n'
    selected = subprocess.run(["bash", "-euc", probe], capture_output=True, text=True, check=True)
    assert selected.stdout.splitlines() == ["new-phase.sh"], "reuse native outputs while retaining later phases"
    assert "MIOS_NATIVE_INSTALL_ROOT=/out bash /build/automation/55-native-build.sh" in recipe, "native compilation must precede the image bake"
    with tempfile.TemporaryDirectory() as directory:
        fixture = Path(directory)
        subprocess.run(["git", "init", "--quiet", directory], check=True)
        omitted = fixture / "omitted consumer.sh"
        retained = fixture / "copied consumer.sh"
        omitted.write_text("tracked source")
        retained.write_text("original source")
        subprocess.run(["git", "-C", directory, "add", "--", omitted.name, retained.name], check=True)
        omitted.unlink()
        retained.write_text("copied operator edit")
        private = fixture / "private-cache"
        private.write_text("untracked cache")
        missing = subprocess.check_output(["git", "-C", directory, "ls-files", "--deleted", "-z"])
        subprocess.run(["git", "-C", directory, "checkout-index", "-z", "--stdin"], input=missing, check=True)
        assert omitted.read_text() == "tracked source"
        assert retained.read_text() == "copied operator edit", "restoration must retain copied edits"
        assert private.read_text() == "untracked cache", "restoration must not modify untracked files"

def test_node_daemon_unit_command():
    """The installed unit invokes the daemon CLI with a supplied SSOT port."""
    root = Path(__file__).resolve().parents[1]
    config = tomllib.loads((root / "usr/share/mios/mios.toml").read_text())
    service = config["units"]["mios-node.service"]["Service"]
    command = service["ExecStart"].split()
    assert command[:2] == ["/usr/bin/mios-node", "run"], "node unit must invoke the run subcommand"
    assert command[2::2] == ["--node-id", "--port"], "node unit must use named CLI arguments"
    assert command[-1] == "${MIOS_PORTS_NODE}", "node port must remain SSOT driven"
    assert "MIOS_PORTS_NODE=${MIOS_PORTS_NODE}" in service["Environment"], "node unit must supply its runtime port"
    assert not any(":-" in value for value in [service["ExecStart"], *service["Environment"]]), "node unit must carry no shell default expressions"
    unit = (root / "usr/lib/systemd/system/mios-node.service").read_text()
    assert f"ExecStart={service['ExecStart']}" in unit, "node unit must match SSOT"
    # systemd expands ${VAR} only in Exec*= lines, so the projection
    # (mios-unit-gen) ships Environment= values with [ports] resolved.
    ports = config["ports"]
    offset = int(ports.get("stack_id", 0)) * 10000
    def resolved(value):
        return re.sub(r"\$\{MIOS_PORTS_([A-Z0-9_]+)\}",
                      lambda m: str(int(ports[m.group(1).lower()]) + offset), value)
    for value in service["Environment"]:
        assert "${" not in resolved(value), f"unresolvable SSOT port in {value}"
        assert f"Environment={resolved(value)}" in unit, "node environment must match SSOT"


if __name__ == "__main__":
    test_node_daemon_unit_command()
    test_build_context_ignores_generated_artifacts()
    test_build_context_preserves_tracked_sources()
    test_self_replication_build_and_digest()
    print("All task_910/task_911 tests passed.")
