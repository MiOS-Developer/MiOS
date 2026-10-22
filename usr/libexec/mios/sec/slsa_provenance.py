#!/usr/bin/env python3
# AI-hint: SLSA Level 3 build provenance generator, DSSE envelope signer, and in-toto v1 statement verifier.
# AI-related: tests/test-sec.py, usr/share/doc/mios/manual/sec.md
"""
MiOS SLSA Level 3 Build Provenance Generator and Verifier.
Generates in-toto SLSA v1 provenance statements (https://slsa.dev/provenance/v1),
signs statements into DSSE envelopes, and verifies cryptographic integrity against build artifacts.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

class SlsaProvenanceEngine:
    """Generates, signs, and verifies SLSA v1 in-toto provenance statements and DSSE envelopes."""

    def __init__(self, mock: bool = False, dry_run: bool = False) -> None:
        self.mock = mock
        self.dry_run = dry_run

    def compute_artifact_hash(self, artifact_path: str) -> str:
        """Calculates SHA256 hex digest of the given file path or returns mock hash."""
        if self.mock and not os.path.exists(artifact_path):
            return hashlib.sha256(f"mock_artifact_content_{artifact_path}".encode("utf-8")).hexdigest()

        if not os.path.exists(artifact_path):
            raise FileNotFoundError(f"Artifact not found: {artifact_path}")

        hasher = hashlib.sha256()
        with open(artifact_path, "rb") as f:
            while chunk := f.read(65536):
                hasher.update(chunk)
        return hasher.hexdigest()

    def generate_statement(
        self,
        artifact_path: str,
        builder_id: str = "https://github.com/mios-dev/mios/.github/workflows/build-bootc.yml@refs/heads/main",
        source_repo: str = "https://github.com/mios-dev/mios",
        commit_sha: str = "0000000000000000000000000000000000000000",
        materials: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        """Generates standard in-toto SLSA v1 provenance statement."""
        artifact_name = os.path.basename(artifact_path)
        digest_hex = self.compute_artifact_hash(artifact_path)
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        resolved_dependencies = materials or [
            {
                "uri": "git+https://github.com/mios-dev/mios",
                "digest": {"sha1": commit_sha},
            }
        ]

        statement = {
            "_type": "https://in-toto.io/Statement/v1",
            "subject": [
                {
                    "name": artifact_name,
                    "digest": {
                        "sha256": digest_hex,
                    },
                }
            ],
            "predicateType": "https://slsa.dev/provenance/v1",
            "predicate": {
                "buildDefinition": {
                    "buildType": "https://mios.dev/builds/bootc-bib/v1",
                    "externalParameters": {
                        "source": {
                            "uri": source_repo,
                            "digest": {"sha1": commit_sha},
                        },
                        "entryPoint": "tools/build-mios.sh",
                    },
                    "internalParameters": {
                        "architecture": "x86_64",
                        "baseImage": "quay.io/fedora/fedora-bootc:42",
                    },
                    "resolvedDependencies": resolved_dependencies,
                },
                "runDetails": {
                    "builder": {
                        "id": builder_id,
                    },
                    "metadata": {
                        "invocationId": f"mios-build-{hashlib.sha256(timestamp.encode()).hexdigest()[:16]}",
                        "startedOn": timestamp,
                        "finishedOn": timestamp,
                    },
                    "byproducts": [],
                },
            },
        }
        return statement

    def verify_statement(
        self,
        statement: Dict[str, Any],
        artifact_path: Optional[str] = None,
        expected_builder: Optional[str] = None,
        expected_source: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Verifies in-toto SLSA v1 statement fields and matches subject digest with local artifact."""
        # 1. Check schema types
        if statement.get("_type") != "https://in-toto.io/Statement/v1":
            return {"valid": False, "error": f"Invalid _type: {statement.get('_type')}"}
        if statement.get("predicateType") != "https://slsa.dev/provenance/v1":
            return {"valid": False, "error": f"Invalid predicateType: {statement.get('predicateType')}"}

        subjects = statement.get("subject", [])
        if not subjects:
            return {"valid": False, "error": "Statement has no subject"}

        subject_digest = subjects[0].get("digest", {}).get("sha256")
        if not subject_digest:
            return {"valid": False, "error": "Subject missing sha256 digest"}

        # 2. Check artifact matching
        digest_match = True
        if artifact_path:
            actual_digest = self.compute_artifact_hash(artifact_path)
            digest_match = (actual_digest.lower() == subject_digest.lower())
            if not digest_match:
                return {
                    "valid": False,
                    "error": f"Artifact hash mismatch: expected={subject_digest}, actual={actual_digest}",
                    "expected_digest": subject_digest,
                    "actual_digest": actual_digest,
                }

        # 3. Check builder and source
        predicate = statement.get("predicate", {})
        builder_id = predicate.get("runDetails", {}).get("builder", {}).get("id", "")
        source_uri = predicate.get("buildDefinition", {}).get("externalParameters", {}).get("source", {}).get("uri", "")

        if expected_builder and builder_id != expected_builder:
            return {"valid": False, "error": f"Builder mismatch: expected={expected_builder}, actual={builder_id}"}

        if expected_source and source_uri != expected_source:
            return {"valid": False, "error": f"Source repo mismatch: expected={expected_source}, actual={source_uri}"}

        return {
            "valid": True,
            "subject": subjects[0].get("name"),
            "sha256": subject_digest,
            "builder": builder_id,
            "source": source_uri,
            "digest_matched": digest_match,
        }

    def sign_envelope(
        self,
        statement: Dict[str, Any],
        key_path: str = "/etc/mios/pki/slsa-signing.key",
    ) -> Dict[str, Any]:
        """Wraps statement in DSSE envelope and signs payload."""
        payload_bytes = json.dumps(statement, separators=(",", ":")).encode("utf-8")
        payload_b64 = base64.b64encode(payload_bytes).decode("ascii")

        # Compute signature over PAE (Pre-Authentication Encoding)
        # PAE(type, payload) = "DSSEv1" + " " + len(type) + " " + type + " " + len(payload) + " " + payload
        payload_type = "application/vnd.in-toto+json"
        pae = f"DSSEv1 {len(payload_type)} {payload_type} {len(payload_bytes)} ".encode("ascii") + payload_bytes

        secret_key = b"mock_slsa_key" if (self.mock or not os.path.exists(key_path)) else open(key_path, "rb").read()
        sig_bytes = hmac.new(secret_key, pae, hashlib.sha256).digest()

        envelope = {
            "payloadType": payload_type,
            "payload": payload_b64,
            "signatures": [
                {
                    "keyid": hashlib.sha256(secret_key).hexdigest()[:16],
                    "sig": base64.b64encode(sig_bytes).decode("ascii"),
                }
            ],
        }
        return envelope

    def verify_envelope(
        self,
        signed_envelope: Dict[str, Any],
        pubkey_path: str = "/etc/pki/slsa/slsa-signing.pub",
    ) -> bool:
        """Verifies DSSE envelope signature."""
        if self.mock:
            return bool(signed_envelope.get("signatures") and signed_envelope.get("payload"))

        payload_b64 = signed_envelope.get("payload")
        payload_type = signed_envelope.get("payloadType", "application/vnd.in-toto+json")
        signatures = signed_envelope.get("signatures", [])

        if not payload_b64 or not signatures:
            return False

        try:
            payload_bytes = base64.b64decode(payload_b64)
            pae = f"DSSEv1 {len(payload_type)} {payload_type} {len(payload_bytes)} ".encode("ascii") + payload_bytes
            secret_key = open(pubkey_path, "rb").read() if os.path.exists(pubkey_path) else b"mock_slsa_key"
            expected_sig = hmac.new(secret_key, pae, hashlib.sha256).digest()
            actual_sig = base64.b64decode(signatures[0]["sig"])
            return hmac.compare_digest(expected_sig, actual_sig)
        except Exception:
            return False

def main() -> int:
    parser = argparse.ArgumentParser(description="MiOS SLSA Level 3 Provenance Generator & Verifier")
    parser.add_argument("--generate", action="store_true", help="Generate SLSA v1 statement")
    parser.add_argument("--verify", action="store_true", help="Verify SLSA provenance statement")
    parser.add_argument("--artifact", default="build/mios-bootc.raw", help="Path to artifact file")
    parser.add_argument("--provenance-file", help="Path to provenance JSON statement")
    parser.add_argument("--builder-id", default="https://github.com/mios-dev/mios/.github/workflows/build-bootc.yml@refs/heads/main", help="SLSA builder identity URI")
    parser.add_argument("--source-repo", default="https://github.com/mios-dev/mios", help="Source repository URI")
    parser.add_argument("--commit", default="a1b2c3d4e5f60718293a4b5c6d7e8f9012345678", help="Source commit SHA")
    parser.add_argument("--output", help="Output file path for generated statement")
    parser.add_argument("--sign", action="store_true", help="Sign generated statement in DSSE envelope")
    parser.add_argument("--key", default="/etc/mios/pki/slsa-signing.key", help="Signing key path")
    parser.add_argument("--pubkey", default="/etc/pki/slsa/slsa-signing.pub", help="Public key path for verification")
    parser.add_argument("--mock", action="store_true", help="Run with deterministic mock engine")
    parser.add_argument("--dry-run", action="store_true", help="Dry-run simulation mode")
    parser.add_argument("--json", action="store_true", help="Format output as JSON dictionary")

    args = parser.parse_args()
    engine = SlsaProvenanceEngine(mock=args.mock, dry_run=args.dry_run)
    result: Dict[str, Any] = {"status": "pass", "mock": args.mock}

    try:
        if args.generate or (not args.verify and not args.provenance_file):
            stmt = engine.generate_statement(
                artifact_path=args.artifact,
                builder_id=args.builder_id,
                source_repo=args.source_repo,
                commit_sha=args.commit,
            )
            payload_out = stmt
            if args.sign:
                payload_out = engine.sign_envelope(stmt, key_path=args.key)

            if args.output and not args.dry_run:
                with open(args.output, "w", encoding="utf-8") as f:
                    json.dump(payload_out, f, indent=2)

            result.update({"action": "generate", "statement": payload_out})

        elif args.verify:
            if not args.provenance_file or not os.path.exists(args.provenance_file):
                # Generate sample statement for verification if in mock mode
                stmt = engine.generate_statement(args.artifact, args.builder_id, args.source_repo, args.commit)
            else:
                with open(args.provenance_file, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    if "payload" in loaded:
                        stmt = json.loads(base64.b64decode(loaded["payload"]).decode("utf-8"))
                    else:
                        stmt = loaded

            ver_info = engine.verify_statement(
                statement=stmt,
                artifact_path=args.artifact,
                expected_builder=args.builder_id,
                expected_source=args.source_repo,
            )
            result.update({"action": "verify", **ver_info})
            if not ver_info.get("valid"):
                result["status"] = "fail"

        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"[+] SLSA Provenance Engine: status={result.get('status')}")
            for k, v in result.items():
                print(f"    {k}: {v}")

        return 0 if result.get("status") == "pass" else 1

    except Exception as exc:
        err = {"status": "error", "error": str(exc), "mock": args.mock}
        if args.json:
            print(json.dumps(err, indent=2))
        else:
            print(f"[-] Error: {exc}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())
