#!/usr/bin/env python3
# AI-hint: Cosign signing and fail-closed verification CLI regression tests using isolated mock tools.
# AI-doc: usr/share/doc/mios/manual/ch18-supply-chain-and-image-integrity.md
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

_ROOT = Path(__file__).resolve().parents[1]
_PUBLISH_BIN = _ROOT / "usr/libexec/mios/mios-cosign-publish"
_REGISTRY_SVC = _ROOT / "usr/lib/systemd/system/mios-registry.service"
_DIGEST = "sha256:" + "a" * 64
_OTHER_DIGEST = "sha256:" + "b" * 64
_IMAGE = "localhost:5000/mios:latest"
_SECRET_MARKER = "private-signer-output-must-not-escape"


def _payload(digest=_DIGEST):
    return {"critical": {"image": {"docker-manifest-digest": digest}}}


class TestCosignPublish(unittest.TestCase):
    """Exercise the actual CLI and child-process failures without keys or registry access."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mios-signature-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.key = self.root / "fixture.key"
        self.pubkey = self.root / "fixture.pub"
        self.key.write_text("non-secret fake signer fixture\n")
        self.pubkey.write_text("non-secret fake verifier fixture\n")
        self.calls = self.root / "calls.jsonl"
        self.env = {
            **os.environ,
            "PATH": str(self.bin),
            "MIOS_TEST_CALLS": str(self.calls),
            "MIOS_TEST_VERIFY_JSON": json.dumps([_payload()]),
            "MIOS_TEST_FAIL_ACTION": "",
            "MIOS_TEST_SECRET": _SECRET_MARKER,
        }

    def install_cosign(self):
        stub = (
            "#!" + sys.executable + "\n"
            "import json, os, sys\n"
            "with open(os.environ['MIOS_TEST_CALLS'], 'a') as f:\n"
            "    f.write(json.dumps(sys.argv[1:]) + '\\n')\n"
            "if sys.argv[1] == os.environ.get('MIOS_TEST_FAIL_ACTION'):\n"
            "    print(os.environ['MIOS_TEST_SECRET'])\n"
            "    print(os.environ['MIOS_TEST_SECRET'], file=sys.stderr)\n"
            "    raise SystemExit(7)\n"
            "if sys.argv[1] == 'verify':\n"
            "    print(os.environ['MIOS_TEST_VERIFY_JSON'])\n"
            "else:\n"
            "    print(os.environ['MIOS_TEST_SECRET'])\n"
        )
        executable = self.bin / "cosign"
        executable.write_text(stub)
        executable.chmod(0o755)
        return executable

    def invoke(self, *args, image=_IMAGE):
        return subprocess.run(
            [sys.executable, str(_PUBLISH_BIN), "--json", "--image", image,
             "--key", str(self.key), "--pubkey", str(self.pubkey), *args],
            env=self.env, capture_output=True, text=True,
        )

    def actions(self):
        if not self.calls.exists():
            return []
        return [json.loads(line) for line in self.calls.read_text().splitlines()]

    def rejected(self, result, verify_only=False):
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        data = json.loads(result.stdout)
        proof = data if verify_only else data["verification"]
        self.assertIs(proof["verified"], False)
        self.assertIs(proof["permitted_for_switch"], False)
        self.assertIsNone(proof["digest"])
        if not verify_only:
            self.assertIs(data["ready_for_switch"], False)
        self.assertNotIn(_SECRET_MARKER, result.stdout + result.stderr)
        return data

    def test_binaries_and_units_exist(self):
        self.assertTrue(_PUBLISH_BIN.is_file())
        self.assertTrue(os.access(_PUBLISH_BIN, os.X_OK))
        self.assertTrue(_REGISTRY_SVC.is_file())

    def test_registry_service_syntax(self):
        content = _REGISTRY_SVC.read_text()
        for section in ("[Unit]", "[Service]", "[Install]"):
            self.assertIn(section, content)
        self.assertIn("${MIOS_NETWORK_LOOPBACK:-127.0.0.1}", content)
        self.assertIn("${MIOS_PORTS_REGISTRY:-5000}", content)
        self.assertIn("${MIOS_PORTS_REGISTRY_INTERNAL:-5000}", content)
        self.assertIn("docker.io/library/registry:2", content)

    def test_real_tool_sign_then_verify_provides_manifest_proof(self):
        self.install_cosign()
        result = self.invoke()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        data = json.loads(result.stdout)
        self.assertIs(data["signing"]["success"], True)
        self.assertIs(data["signing"]["verified"], False)
        self.assertIs(data["signing"]["permitted_for_switch"], False)
        self.assertIsNone(data["signing"]["signature_ref"])
        self.assertIs(data["verification"]["verified"], True)
        self.assertIs(data["verification"]["permitted_for_switch"], True)
        self.assertEqual(_DIGEST, data["verification"]["digest"])
        self.assertIs(data["ready_for_switch"], True)
        calls = self.actions()
        self.assertEqual(["sign", "verify"], [c[0] for c in calls])
        self.assertIn("--tlog-upload=false", calls[0])
        self.assertEqual(["--output", "json"], calls[1][-3:-1])
        self.assertEqual(_IMAGE, calls[0][-1])
        self.assertEqual(_IMAGE, calls[1][-1])
        self.assertNotIn(_SECRET_MARKER, result.stdout + result.stderr)

    def test_verify_only_does_not_require_private_signer(self):
        self.install_cosign()
        self.key.unlink()
        result = self.invoke("--verify-only", image="localhost:5000/unsigned-name@" + _DIGEST)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIs(json.loads(result.stdout)["permitted_for_switch"], True)
        self.assertEqual(["verify"], [c[0] for c in self.actions()])

    def test_missing_cosign_rejects_sign_and_verify(self):
        for args in ((), ("--verify-only",)):
            with self.subTest(args=args):
                result = self.invoke(*args)
                data = self.rejected(result, bool(args))
                failure = data if args else data["signing"]
                self.assertIn("cosign is unavailable", failure["error"])
                self.assertEqual([], self.actions())

    def test_simulations_never_sign_verify_or_permit_switch(self):
        self.install_cosign()
        for flag in ("--mock", "--dry-run"):
            for args in ((), ("--verify-only",)):
                with self.subTest(flag=flag, args=args):
                    data = self.rejected(self.invoke(flag, *args), bool(args))
                    simulated = data if args else data["signing"]
                    self.assertIs(simulated["simulated"], True)
                    if not args:
                        self.assertIs(data["signing"]["success"], False)
        self.assertEqual([], self.actions())

    def test_missing_empty_directory_and_broken_signer_reject_without_tool_calls(self):
        self.install_cosign()
        for kind in ("missing", "empty", "directory", "broken"):
            with self.subTest(kind=kind):
                self.key.unlink(missing_ok=True)
                if kind == "empty":
                    self.key.touch()
                elif kind == "directory":
                    self.key.mkdir()
                elif kind == "broken":
                    self.key.symlink_to(self.root / "absent-key")
                data = self.rejected(self.invoke())
                self.assertIs(data["signing"]["success"], False)
                self.assertIn("readable, non-empty file", data["signing"]["error"])
                if kind == "directory":
                    self.key.rmdir()
        self.assertEqual([], self.actions())

    def test_missing_public_key_rejects_verification(self):
        self.install_cosign()
        self.pubkey.unlink()
        for args in ((), ("--verify-only",)):
            with self.subTest(args=args):
                data = self.rejected(self.invoke(*args), bool(args))
                proof = data if args else data["verification"]
                self.assertIn("readable, non-empty file", proof["error"])
        self.assertEqual(["sign"], [c[0] for c in self.actions()])

    def test_failed_signer_never_runs_verification_or_leaks_output(self):
        self.install_cosign()
        self.env["MIOS_TEST_FAIL_ACTION"] = "sign"
        data = self.rejected(self.invoke())
        self.assertIs(data["signing"]["success"], False)
        self.assertIn("exit 7", data["signing"]["error"])
        self.assertEqual(["sign"], [c[0] for c in self.actions()])

    def test_failed_verifier_after_sign_never_permits_switch(self):
        self.install_cosign()
        self.env["MIOS_TEST_FAIL_ACTION"] = "verify"
        data = self.rejected(self.invoke())
        self.assertIs(data["signing"]["success"], True)
        self.assertIn("exit 7", data["verification"]["error"])
        self.assertEqual(["sign", "verify"], [c[0] for c in self.actions()])

    def test_failed_verify_only_never_leaks_command_output(self):
        self.install_cosign()
        self.env["MIOS_TEST_FAIL_ACTION"] = "verify"
        self.rejected(self.invoke("--verify-only"), True)

    def test_executable_launch_error_is_bounded_json(self):
        executable = self.install_cosign()
        executable.write_text("invalid executable format\n")
        data = self.rejected(self.invoke())
        self.assertIn("could not run", data["signing"]["error"])
        self.assertEqual([], self.actions())

    def test_success_exit_without_valid_manifest_proof_is_rejected(self):
        self.install_cosign()
        for output in ("", "not-json", "null", "{}", "[]", "[null]", "[7]",
                       json.dumps([{}]), json.dumps([_payload("not-a-digest")]),
                       json.dumps([_payload(_DIGEST), _payload(_OTHER_DIGEST)])):
            with self.subTest(output=output):
                self.env["MIOS_TEST_VERIFY_JSON"] = output
                data = self.rejected(self.invoke("--verify-only"), True)
                self.assertIn("no unambiguous manifest proof", data["error"])

    def test_verified_digest_must_match_requested_digest(self):
        self.install_cosign()
        result = self.invoke("--verify-only", image="localhost:5000/mios@" + _OTHER_DIGEST)
        self.rejected(result, True)

    def test_multiple_signatures_for_one_digest_are_valid(self):
        self.install_cosign()
        self.env["MIOS_TEST_VERIFY_JSON"] = json.dumps([_payload(), _payload()])
        result = self.invoke("--verify-only")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual(_DIGEST, json.loads(result.stdout)["digest"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
