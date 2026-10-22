#!/usr/bin/env python3
# AI-hint: Cryptographic Merkle-tree agent audit chain recorder and Ed25519 block signer.
# AI-related: tests/test-ai.py
# AI-functions: AuditChainRecorder, MerkleTree, generate_ed25519_keypair, main
"""
WS-AI (task_497): Cryptographic Merkle-Tree Agent Audit Chain Recorder & Ed25519 Block Signer.
Maintains an immutable, append-only cryptographic audit chain for all agent decisions,
tool invocations, and filesystem modifications. Each block cryptographically binds to its predecessor
via SHA-256 hash chains, signs the block hash using the host node's Ed25519 key, and constructs
periodic Merkle tree roots for lightweight inclusion proof verification and tamper detection.
"""

from __future__ import annotations

import argparse
import base64
import datetime
import hashlib
import hmac
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_AUDIT_LOG_PATH = "/var/lib/mios/ai/audit/audit_chain.jsonl"
DEFAULT_KEY_PATH = "/var/lib/mios/ai/audit/node_ed25519.key"
GENESIS_PREV_HASH = "0" * 64

# Native Ed25519 comes from python3-cryptography. There is deliberately NO
# fallback: a key derived from a constant (the old sha256-of-a-literal seed) is
# public, so anything "signed" with it is forgeable. Without the library the
# signer refuses to sign and every signature check fails.
try:
    from cryptography.hazmat.primitives.asymmetric import ed25519 as crypto_ed25519
    _HAS_CRYPTOGRAPHY = True
except ImportError:
    _HAS_CRYPTOGRAPHY = False

_MOCK_PUBLIC_KEY = "mock_ed25519_pubkey_0123456789abcdef0123456789abcdef"


class AuditKeyError(RuntimeError):
    """No usable, persisted node signing key: the audit chain fails closed."""


class Ed25519Signer:
    """Ed25519 signer bound to ONE persisted node key (``key_path``, raw 32-byte seed, 0600).

    The key is generated on first sign and must be persisted before it is used:
    a key that is not on disk can never be re-verified by a later run, so a
    block signed with it would silently break the chain. ``mock`` is a test seam
    only; it is honoured solely when the caller passes ``mock=True``.
    """

    def __init__(self, key_path: Optional[str] = None, mock: bool = False) -> None:
        self.key_path = key_path or DEFAULT_KEY_PATH
        self.mock = bool(mock)
        self._private_key: Any = None
        self._public_key_hex: str = ""
        self.key_error: str = ""
        self._init_keys()

    def _init_keys(self) -> None:
        if self.mock:
            self._public_key_hex = _MOCK_PUBLIC_KEY
            return
        if not _HAS_CRYPTOGRAPHY:
            self.key_error = ("python3-cryptography is unavailable: there is no Ed25519 "
                              "implementation, so audit blocks can be neither signed nor verified")
            return
        if os.path.lexists(self.key_path):
            self._load_key()
        # else: not provisioned yet -- generated (and persisted) on first sign.

    def _load_key(self) -> None:
        try:
            with open(self.key_path, "rb") as f:
                raw = f.read()
            if len(raw) != 32:
                raise ValueError(f"expected a 32-byte Ed25519 seed, found {len(raw)} bytes")
            self._set_key(crypto_ed25519.Ed25519PrivateKey.from_private_bytes(raw))
        except Exception as exc:  # noqa: BLE001 -- recorded, then raised by sign()
            # Never replace an unreadable key: a fresh one would orphan every
            # block it signed. Refuse until an operator repairs or rotates it.
            self.key_error = (f"node key {self.key_path} is unreadable or invalid ({exc}); "
                              "refusing to sign or verify with a substitute key")

    def _set_key(self, key: Any) -> None:
        self._private_key = key
        self._public_key_hex = key.public_key().public_bytes_raw().hex()

    def _generate_and_persist(self) -> None:
        key = crypto_ed25519.Ed25519PrivateKey.generate()
        parent = os.path.dirname(self.key_path)
        try:
            if parent:
                os.makedirs(parent, mode=0o700, exist_ok=True)
            fd = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                         | getattr(os, "O_BINARY", 0), 0o600)
        except OSError as exc:
            if isinstance(exc, FileExistsError) and os.path.lexists(self.key_path):
                self._load_key()  # a concurrent writer provisioned it first
                return
            self.key_error = (f"cannot persist a new node key at {self.key_path} ({exc}); "
                              "refusing to sign with a key no later run could verify")
            return
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(key.private_bytes_raw())
                f.flush()
                os.fsync(f.fileno())
        except OSError as exc:
            try:
                os.unlink(self.key_path)
            except OSError:
                pass
            self.key_error = (f"cannot persist a new node key at {self.key_path} ({exc}); "
                              "refusing to sign with a key no later run could verify")
            return
        self._set_key(key)

    @property
    def public_key_hex(self) -> str:
        """The trusted node public key, or "" when none is provisioned/usable."""
        return self._public_key_hex

    def sign(self, message: bytes | str) -> str:
        data = message.encode("utf-8") if isinstance(message, str) else message
        if self.mock:
            return hashlib.sha256(b"mock-sig:" + data).hexdigest()
        if self._private_key is None and not self.key_error:
            self._generate_and_persist()
        if self.key_error or self._private_key is None:
            raise AuditKeyError(self.key_error or f"no node signing key at {self.key_path}")
        return self._private_key.sign(data).hex()

    def verify(self, message: bytes | str, signature_hex: str, public_key_hex: str) -> bool:
        data = message.encode("utf-8") if isinstance(message, str) else message
        if self.mock:
            expected = hashlib.sha256(b"mock-sig:" + data).hexdigest()
            return public_key_hex == _MOCK_PUBLIC_KEY and hmac.compare_digest(signature_hex, expected)
        if not _HAS_CRYPTOGRAPHY:
            return False
        try:
            pub_key = crypto_ed25519.Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
            pub_key.verify(bytes.fromhex(signature_hex), data)
            return True
        except Exception:  # noqa: BLE001 -- any malformed key/signature is a failed check
            return False

class MerkleTree:
    """Binary Merkle tree implementation for batched block verification."""

    def __init__(self, leaf_hashes: List[str]) -> None:
        self.leaf_hashes = leaf_hashes
        self.levels: List[List[str]] = []
        self._build_tree()

    def _hash_pair(self, left: str, right: str) -> str:
        return hashlib.sha256(f"{left}{right}".encode("utf-8")).hexdigest()

    def _build_tree(self) -> None:
        if not self.leaf_hashes:
            self.levels = [["0" * 64]]
            return

        current_level = list(self.leaf_hashes)
        self.levels.append(current_level)

        while len(current_level) > 1:
            next_level: List[str] = []
            for i in range(0, len(current_level), 2):
                left = current_level[i]
                right = current_level[i + 1] if i + 1 < len(current_level) else left
                next_level.append(self._hash_pair(left, right))
            self.levels.append(next_level)
            current_level = next_level

    @property
    def root(self) -> str:
        return self.levels[-1][0] if self.levels and self.levels[-1] else "0" * 64

    def get_proof(self, index: int) -> List[Dict[str, str]]:
        """Generate audit inclusion proof for leaf at given index."""
        if index < 0 or index >= len(self.leaf_hashes):
            return []

        proof: List[Dict[str, str]] = []
        idx = index

        for level in self.levels[:-1]:
            is_right = idx % 2 == 1
            sibling_idx = idx - 1 if is_right else idx + 1
            if sibling_idx < len(level):
                proof.append({
                    "position": "left" if is_right else "right",
                    "hash": level[sibling_idx],
                })
            else:
                proof.append({
                    "position": "right",
                    "hash": level[idx],
                })
            idx //= 2

        return proof

    @classmethod
    def verify_proof(cls, leaf_hash: str, proof: List[Dict[str, str]], expected_root: str) -> bool:
        """Verify that a leaf hash belongs to the Merkle tree with root expected_root."""
        current = leaf_hash
        for step in proof:
            pos = step.get("position")
            sibling = step.get("hash", "")
            if pos == "left":
                current = hashlib.sha256(f"{sibling}{current}".encode("utf-8")).hexdigest()
            else:
                current = hashlib.sha256(f"{current}{sibling}".encode("utf-8")).hexdigest()
        return current == expected_root

def compute_payload_hash(payload: Any) -> str:
    """Deterministic SHA-256 hash of JSON payload."""
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

def compute_block_hash(
    index: int,
    timestamp: str,
    prev_hash: str,
    agent_id: str,
    event_type: str,
    payload_hash: str,
) -> str:
    """Compute canonical SHA-256 block hash."""
    raw = f"{index}:{timestamp}:{prev_hash}:{agent_id}:{event_type}:{payload_hash}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

class AuditChainRecorder:
    """Recorder maintaining the append-only cryptographic audit chain."""

    def __init__(
        self,
        log_path: str = DEFAULT_AUDIT_LOG_PATH,
        key_path: str = DEFAULT_KEY_PATH,
        agent_id: str = "mios-agent-primary",
        mock: bool = False,
        verbose: bool = False,
    ) -> None:
        self.log_path = log_path
        self.agent_id = agent_id
        self.mock = mock
        self.verbose = verbose
        self.signer = Ed25519Signer(key_path=key_path, mock=mock)
        self._in_memory_blocks: List[Dict[str, Any]] = []

    def load_blocks(self) -> List[Dict[str, Any]]:
        """Load all blocks from disk or in-memory state."""
        if self.mock and self._in_memory_blocks:
            return self._in_memory_blocks

        blocks: List[Dict[str, Any]] = []
        if os.path.isfile(self.log_path):
            try:
                with open(self.log_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            blocks.append(json.loads(line))
            except Exception as exc:
                if self.verbose:
                    sys.stderr.write(f"[audit-chain] Read error: {exc}\n")

        if not blocks and self.mock:
            # Initialize with genesis block in mock mode
            genesis = self._create_genesis_block()
            self._in_memory_blocks = [genesis]
            return self._in_memory_blocks

        return blocks

    def _create_genesis_block(self) -> Dict[str, Any]:
        """Create genesis block with index 0 and zero previous hash."""
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        payload = {"genesis": "MiOS Agent Cryptographic Audit Root", "version": "1.0"}
        p_hash = compute_payload_hash(payload)
        b_hash = compute_block_hash(0, now, GENESIS_PREV_HASH, self.agent_id, "genesis", p_hash)
        sig = self.signer.sign(b_hash)

        return {
            "index": 0,
            "timestamp": now,
            "prev_hash": GENESIS_PREV_HASH,
            "agent_id": self.agent_id,
            "event_type": "genesis",
            "payload": payload,
            "payload_hash": p_hash,
            "block_hash": b_hash,
            "public_key": self.signer.public_key_hex,
            "signature": sig,
        }

    def record_event(
        self,
        event_type: str,
        payload: Dict[str, Any],
        custom_agent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Append a new signed block to the cryptographic audit chain."""
        blocks = self.load_blocks()
        if blocks and not self.mock and blocks[-1].get("public_key") != self.signer.public_key_hex:
            # A lost or replaced node key must not quietly fork the chain onto a new key.
            raise AuditKeyError(self.signer.key_error or (
                f"the head of {self.log_path} is not signed by the node key at {self.signer.key_path}; "
                "refusing to extend it under a different key"))
        if not blocks:
            genesis = self._create_genesis_block()
            blocks = [genesis]
            if not self.mock:
                self._append_block_to_file(genesis)
            else:
                self._in_memory_blocks.append(genesis)

        last_block = blocks[-1]
        next_index = last_block["index"] + 1
        prev_hash = last_block["block_hash"]
        aid = custom_agent_id or self.agent_id
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()

        p_hash = compute_payload_hash(payload)
        b_hash = compute_block_hash(next_index, now, prev_hash, aid, event_type, p_hash)
        sig = self.signer.sign(b_hash)

        block = {
            "index": next_index,
            "timestamp": now,
            "prev_hash": prev_hash,
            "agent_id": aid,
            "event_type": event_type,
            "payload": payload,
            "payload_hash": p_hash,
            "block_hash": b_hash,
            "public_key": self.signer.public_key_hex,
            "signature": sig,
        }

        if self.mock:
            self._in_memory_blocks.append(block)
        else:
            self._append_block_to_file(block)

        return {
            "success": True,
            "status": "block_recorded",
            "index": next_index,
            "block_hash": b_hash,
            "prev_hash": prev_hash,
            "block": block,
        }

    def _append_block_to_file(self, block: Dict[str, Any]) -> None:
        parent = os.path.dirname(self.log_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(block, sort_keys=True) + "\n")

    def verify_chain(self, blocks: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """
        Verify complete cryptographic chain continuity:
        1. Genesis block correctness.
        2. Hash chain continuity (block[i].prev_hash == block[i-1].block_hash).
        3. Payload hash integrity.
        4. Block hash integrity.
        5. Ed25519 signature validity.
        """
        chain = blocks if blocks is not None else self.load_blocks()
        if not chain:
            return {
                "valid": True,
                "status": "empty_chain",
                "blocks_verified": 0,
                "merkle_root": "0" * 64,
            }

        # Signatures verify against the node's own persisted key, never the key a
        # block carries: a block naming its own key is self-certifying, so a
        # rewritten chain re-signed under any attacker key would pass.
        trusted_key = self.signer.public_key_hex
        if not trusted_key:
            return {
                "valid": False,
                "status": "no_trusted_key",
                "error": self.signer.key_error or f"no provisioned node key at {self.signer.key_path}",
                "failed_block_index": 0,
            }

        # Check genesis
        genesis = chain[0]
        if genesis["index"] != 0 or genesis["prev_hash"] != GENESIS_PREV_HASH:
            return {
                "valid": False,
                "status": "invalid_genesis",
                "error": "Genesis block prev_hash or index is invalid",
                "failed_block_index": 0,
            }

        leaf_hashes: List[str] = []

        for i, blk in enumerate(chain):
            leaf_hashes.append(blk["block_hash"])

            # Check index sequence
            if blk["index"] != i:
                return {
                    "valid": False,
                    "status": "index_sequence_broken",
                    "error": f"Block index {blk['index']} mismatch expected {i}",
                    "failed_block_index": i,
                }

            # Check prev_hash link
            if i > 0:
                expected_prev = chain[i - 1]["block_hash"]
                if blk["prev_hash"] != expected_prev:
                    return {
                        "valid": False,
                        "status": "broken_hash_link",
                        "error": f"Block {i} prev_hash does not match block {i-1} block_hash",
                        "failed_block_index": i,
                    }

            # Verify payload hash
            recalc_p_hash = compute_payload_hash(blk["payload"])
            if recalc_p_hash != blk.get("payload_hash"):
                return {
                    "valid": False,
                    "status": "payload_tampered",
                    "error": f"Payload hash mismatch at block {i}",
                    "failed_block_index": i,
                }

            # Verify block hash
            recalc_b_hash = compute_block_hash(
                blk["index"],
                blk["timestamp"],
                blk["prev_hash"],
                blk["agent_id"],
                blk["event_type"],
                recalc_p_hash,
            )
            if recalc_b_hash != blk.get("block_hash"):
                return {
                    "valid": False,
                    "status": "block_hash_tampered",
                    "error": f"Block hash recalculation mismatch at block {i}",
                    "failed_block_index": i,
                }

            if blk.get("public_key") != trusted_key:
                return {
                    "valid": False,
                    "status": "untrusted_signer",
                    "error": f"Block {i} is signed by a key other than this node's key",
                    "failed_block_index": i,
                }

            # Verify signature
            valid_sig = self.signer.verify(
                recalc_b_hash,
                blk.get("signature", ""),
                trusted_key,
            )
            if not valid_sig:
                return {
                    "valid": False,
                    "status": "signature_invalid",
                    "error": f"Ed25519 signature verification failed at block {i}",
                    "failed_block_index": i,
                }

        tree = MerkleTree(leaf_hashes)
        return {
            "valid": True,
            "status": "verified",
            "blocks_verified": len(chain),
            "merkle_root": tree.root,
            "head_block_hash": chain[-1]["block_hash"],
        }

def main() -> int:
    parser = argparse.ArgumentParser(
        description="MiOS Cryptographic Merkle Audit Chain & Block Signer (task_497)"
    )
    parser.add_argument("--record", action="store_true", help="Record new audit event")
    parser.add_argument("--event", default="decision", help="Event type identifier")
    parser.add_argument("--payload", help="JSON string of event payload")
    parser.add_argument("--agent-id", default="mios-agent-primary", help="Recording agent identifier")
    parser.add_argument("--verify", action="store_true", help="Verify entire audit chain integrity")
    parser.add_argument("--dump", action="store_true", help="Dump all recorded audit blocks")
    parser.add_argument("--status", action="store_true", help="Display chain summary status")
    parser.add_argument("--log-path", default=DEFAULT_AUDIT_LOG_PATH, help="Path to audit log")
    parser.add_argument("--mock", action="store_true", help="Run with simulated mocks")
    parser.add_argument("--json", action="store_true", help="Output results in JSON format")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")

    args = parser.parse_args()

    recorder = AuditChainRecorder(
        log_path=args.log_path,
        agent_id=args.agent_id,
        mock=args.mock,
        verbose=args.verbose,
    )

    result: Dict[str, Any] = {}

    if args.record:
        payload_data = {}
        if args.payload:
            try:
                payload_data = json.loads(args.payload)
            except Exception as exc:
                sys.stderr.write(f"Error parsing payload JSON: {exc}\n")
                return 1
        else:
            payload_data = {"action": "cli_invoked", "args": sys.argv[1:]}
        try:
            result = recorder.record_event(args.event, payload_data)
        except AuditKeyError as exc:
            sys.stderr.write(f"[audit-chain] refusing to record: {exc}\n")
            result = {"success": False, "status": "signing_unavailable", "error": str(exc)}
    elif args.verify:
        result = recorder.verify_chain()
    elif args.dump:
        blocks = recorder.load_blocks()
        result = {"total_blocks": len(blocks), "blocks": blocks}
    elif args.status or len(sys.argv) == 1:
        blocks = recorder.load_blocks()
        ver = recorder.verify_chain(blocks)
        result = {
            "total_blocks": len(blocks),
            "chain_valid": ver.get("valid", False),
            "merkle_root": ver.get("merkle_root"),
            "head_hash": blocks[-1]["block_hash"] if blocks else None,
            "public_key": recorder.signer.public_key_hex,
            "key_error": recorder.signer.key_error or None,
        }
    else:
        parser.print_help()
        return 1

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(json.dumps(result, indent=2))

    # Any failed record or verification is a non-zero exit (was `or`, which
    # turned a refused record and a broken chain into exit 0).
    ok = all(result.get(k, True) for k in ("success", "valid", "chain_valid"))
    return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(main())
