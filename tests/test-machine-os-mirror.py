#!/usr/bin/env python3
# AI-hint: Exercises machine OS mirror publication with isolated registry commands, digest controls, namespace failures, and reusable workflow output contracts.
# AI-related: tools/mirror-machine-os.sh, .github/workflows/machine-os-mirror.yml, usr/share/mios/templates/python-test, usr/share/mios/mios.toml
# AI-functions: main
"""Regression tests for the transferred-repository mirror publisher."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[1]
MIRROR = ROOT / "tools/mirror-machine-os.sh"
DESTINATION = "ghcr.io/mios-developer/machine-os"
DIGESTS = ["sha256:" + "a" * 64, "sha256:" + "b" * 64]
DISK = "sha256:" + "c" * 64

MOCK = """#!/usr/bin/env python3
import json, os, pathlib, sys
tool = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ["MOCK_LOG"], "a") as log:
    log.write(json.dumps([tool] + args) + "\\n")
scenario = os.environ.get("MOCK_SCENARIO", "")
source = json.loads(pathlib.Path(os.environ["MOCK_SOURCE"]).read_text())
if tool == "skopeo":
    assert args[:2] == ["inspect", "--raw"], args
    if args[2] == "docker://quay.io/podman/machine-os:6.1":
        if scenario == "empty":
            source["manifests"] = source["manifests"][2:]
        elif scenario == "missing-digest":
            source["manifests"].append({"platform": {"os": "linux", "architecture": "riscv64"}})
        print(json.dumps(source))
        sys.exit(42 if scenario == "source-fails-with-output" else 0)
    target = [item for item in source["manifests"] if "disktype" not in item.get("annotations", {})]
    target.reverse()
    if scenario == "mismatch":
        target[0] = dict(target[0], digest="sha256:" + "d" * 64)
    elif scenario == "extra-disk":
        target.append(source["manifests"][2])
    elif scenario == "missing-arch":
        target.pop()
    print(json.dumps({"schemaVersion": 2, "manifests": target}))
    sys.exit(43 if scenario == "destination-fails-with-output" else 0)
assert tool == "podman" and args[0] == "manifest", (tool, args)
if args[1] == "exists":
    sys.exit(0 if scenario == "existing-local" else 125 if scenario == "storage-fails" else 1)
if args[1] == "push" and scenario == "push-fails":
    sys.exit(44)
print("ok")
"""


class TestMirrorCommand(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="mios-mirror-tests-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / "chosen checkout"
        vendor = self.root / "usr/share/mios"
        vendor.mkdir(parents=True)
        self.toml = vendor / "mios.toml"
        self.write_ssot()
        getter = self.root / "usr/libexec/mios/mios-toml-get"
        getter.parent.mkdir(parents=True)
        shutil.copy2(ROOT / "usr/libexec/mios/mios-toml-get", getter)
        library = self.root / "usr/lib/mios"
        library.parent.mkdir(parents=True)
        library.symlink_to(ROOT / "usr/lib/mios", target_is_directory=True)
        self.bin = self.base / "bin"
        self.bin.mkdir()
        for name in ("skopeo", "podman"):
            path = self.bin / name
            path.write_text(MOCK)
            path.chmod(0o755)
        self.log = self.base / "commands.jsonl"
        self.output = self.base / "github-output"
        self.source = self.base / "source.json"
        self.source.write_text(json.dumps({
            "schemaVersion": 2,
            "manifests": [
                {"digest": DIGESTS[0], "platform": {"os": "linux", "architecture": "amd64"}},
                {"digest": DIGESTS[1], "platform": {"os": "linux", "architecture": "arm64"}},
                {"digest": DISK, "annotations": {"disktype": "qcow2"}},
            ],
        }))

    def write_ssot(self, ci_image=None):
        self.toml.write_text(
            '[image]\nmachine_os_repo = "quay.io/podman/machine-os"\n'
            'machine_os_tag = "6.1"\nmachine_os_mirror = "' + DESTINATION + '"\n'
            '[ci.fedora]\nimage = "' + (ci_image or DESTINATION + ":6.1") + '"\n'
        )

    def run_mirror(self, scenario="", owner="MiOS-Developer", override=None):
        self.log.unlink(missing_ok=True)
        self.output.unlink(missing_ok=True)
        env = dict(os.environ)
        for name in ("BASH_ENV", "ENV", "GITHUB_TOKEN", "GH_TOKEN"):
            env.pop(name, None)
        env.update(
            PATH=str(self.bin) + os.pathsep + env["PATH"],
            MOCK_LOG=str(self.log), MOCK_SOURCE=str(self.source), MOCK_SCENARIO=scenario,
            GITHUB_ACTIONS="true", GITHUB_REPOSITORY_OWNER=owner,
            GITHUB_OUTPUT=str(self.output), PYTHONDONTWRITEBYTECODE="1",
            # The command must deliberately select its supplied checkout.
            MIOS_TOML_ROOT="/mios-unrelated-source",
            MIOS_VENDOR_TOML="/mios-unrelated-source/mios.toml",
        )
        args = ["bash", str(MIRROR), str(self.root)]
        if override is not None:
            args.append(override)
        return subprocess.run(args, env=env, text=True, capture_output=True, timeout=30)

    def commands(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def assert_not_published(self):
        self.assertFalse(any(command[:3] == ["podman", "manifest", "push"] for command in self.commands()))
        self.assertFalse(self.output.exists())

    def test_both_architectures_are_mirrored_without_disk_artifacts(self):
        result = self.run_mirror()
        self.assertEqual(result.returncode, 0, result.stderr)
        commands = self.commands()
        added = [command[-1] for command in commands if command[:3] == ["podman", "manifest", "add"]]
        self.assertEqual(added, ["docker://quay.io/podman/machine-os@" + digest for digest in DIGESTS])
        pushed = [command for command in commands if command[:3] == ["podman", "manifest", "push"]]
        self.assertEqual(len(pushed), 1)
        self.assertIn("--all", pushed[0])
        self.assertEqual(pushed[0][-1], "docker://" + DESTINATION + ":6.1")
        self.assertEqual(self.output.read_text(), "image=" + DESTINATION + ":6.1\n")
        self.assertEqual(commands[-1][:3], ["podman", "manifest", "rm"])
        self.assertIn("2 container image(s), digest-identical", result.stdout)

    def test_stale_owner_fails_before_any_registry_or_storage_command(self):
        result = self.run_mirror(owner="another-owner")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("outside repository owner another-owner", result.stderr)
        self.assertEqual(self.commands(), [])
        self.assert_not_published()

    def test_ci_image_disagreement_fails_before_publication(self):
        self.write_ssot(ci_image="ghcr.io/old-owner/machine-os:6.1")
        result = self.run_mirror()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ci.fedora.image must equal", result.stderr)
        self.assertEqual(self.commands(), [])

    def test_inspection_failure_cannot_publish_even_with_valid_stdout(self):
        result = self.run_mirror("source-fails-with-output")
        self.assertEqual(result.returncode, 42, result.stderr)
        self.assertEqual(len(self.commands()), 1)
        self.assert_not_published()

    def test_empty_or_malformed_container_set_is_not_published(self):
        for scenario in ("empty", "missing-digest"):
            with self.subTest(scenario=scenario):
                result = self.run_mirror(scenario)
                self.assertNotEqual(result.returncode, 0)
                self.assert_not_published()

    def test_failed_push_cleans_only_its_created_manifest(self):
        result = self.run_mirror("push-fails")
        self.assertEqual(result.returncode, 44, result.stderr)
        commands = self.commands()
        created = next(command[-1] for command in commands if command[:3] == ["podman", "manifest", "create"])
        self.assertEqual(commands[-1], ["podman", "manifest", "rm", created])
        self.assertFalse(self.output.exists())

    def test_existing_local_manifest_is_not_removed(self):
        result = self.run_mirror("existing-local")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("refusing existing local manifest", result.stderr)
        self.assertFalse(any(command[:3] == ["podman", "manifest", "rm"] for command in self.commands()))
        self.assert_not_published()

    def test_storage_failure_is_not_treated_as_absence(self):
        result = self.run_mirror("storage-fails")
        self.assertEqual(result.returncode, 125, result.stderr)
        self.assert_not_published()

    def test_post_publish_inspection_failure_does_not_export_ready_image(self):
        result = self.run_mirror("destination-fails-with-output")
        self.assertEqual(result.returncode, 43, result.stderr)
        self.assertEqual(self.commands()[-1][:3], ["podman", "manifest", "rm"])
        self.assertFalse(self.output.exists())

    def test_wrong_missing_or_extra_child_manifest_cannot_report_success(self):
        for scenario in ("mismatch", "missing-arch", "extra-disk"):
            with self.subTest(scenario=scenario):
                result = self.run_mirror(scenario)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("differs from", result.stderr)
                self.assertFalse(self.output.exists())
                self.assertEqual(self.commands()[-1][:3], ["podman", "manifest", "rm"])

    def test_explicit_mirror_override_is_verified(self):
        override = "ghcr.io/mios-developer/machine-os-test"
        result = self.run_mirror(override=override)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.output.read_text(), "image=" + override + ":6.1\n")


class TestMirrorWorkflowContract(unittest.TestCase):
    def test_reusable_output_and_publication_permission_boundary(self):
        import yaml
        workflow = yaml.load((ROOT / ".github/workflows/machine-os-mirror.yml").read_text(), Loader=yaml.BaseLoader)
        self.assertIn("workflow_call", workflow["on"])
        self.assertEqual(workflow["on"]["workflow_call"]["outputs"]["image"]["value"], "${{ jobs.mirror.outputs.image }}")
        job = workflow["jobs"]["mirror"]
        self.assertEqual(job["permissions"]["packages"], "write")
        self.assertIn("pull_request", job["if"])
        self.assertEqual(job["concurrency"]["cancel-in-progress"], "false")
        publisher = next(step for step in job["steps"] if step.get("id") == "mirror")
        self.assertIn("mirror-machine-os.sh", publisher["run"])

    def test_shipped_ci_reference_matches_the_current_package_owner(self):
        data = tomllib.loads((ROOT / "usr/share/mios/mios.toml").read_text())
        mirror = data["image"]["machine_os_mirror"]
        self.assertEqual(mirror.split("/")[1], "mios-developer")
        self.assertEqual(data["ci"]["fedora"]["image"], mirror + ":" + data["image"]["machine_os_tag"])


    def test_publisher_and_held_pull_target_keep_operator_key_enforcement(self):
        data = tomllib.loads((ROOT / "usr/share/mios/mios.toml").read_text())
        image = data["image"]
        scopes = data["security"]["sigstore"]["scopes"]
        old = next(scope for scope in scopes if scope["scope"] == "ghcr.io/mios-dev")
        current = next(scope for scope in scopes if scope["scope"] == image["name"].rsplit("/", 1)[0])
        self.assertEqual(current["scope"], "ghcr.io/mios-developer")
        self.assertEqual(current["type"], "sigstoreSigned")
        self.assertEqual(current["key_paths"], old["key_paths"])
        self.assertEqual(current["signed_identity"], old["signed_identity"])
        reference = image["ref"].split("@", 1)[0]
        if ":" in reference.rsplit("/", 1)[-1]:
            reference = reference.rsplit(":", 1)[0]
        self.assertTrue(any(
            scope["transport"] == "docker"
            and scope["type"] == "sigstoreSigned"
            and (reference == scope["scope"] or reference.startswith(scope["scope"] + "/"))
            for scope in scopes
        ))


def main():
    unittest.main()


if __name__ == "__main__":
    main()
