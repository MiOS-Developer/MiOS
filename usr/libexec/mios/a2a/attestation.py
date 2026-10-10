#!/usr/bin/env python3
# AI-hint: A2A Agent-to-Agent mutual capability exchange and cryptographic attestation protocol engine.
# AI-related: usr/lib/mios/agent-pipe/mios_pipe/federation/agentcard_sign.py, tests/test-a2a.py
"""
MiOS Agent-to-Agent (A2A) Capability Attestation & Key Exchange Engine.

Implements Ed25519 mutual AgentCard signing, RFC-8785 JSON Canonicalization Scheme (JCS),
clock-skew resilient expiration verification, capability negotiation, and CLI toolsuite.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import secrets
import sys
import time
from typing import Any, Dict, List, Optional, Tuple, Union

try:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
        Ed25519PublicKey,
    )
except ImportError:  # pragma: no cover
    raise ImportError("cryptography package is required for A2A attestation")

def canonical_json(obj: Any) -> bytes:
    """RFC-8785 JSON Canonicalization Scheme (JCS) deterministic bytes."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

def _b64u(b: bytes) -> str:
    """RFC-7515 §2 BASE64URL without padding."""
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")

def _b64u_decode(s: str) -> bytes:
    """Decode base64 or base64url string with padding restoration."""
    raw = str(s or "").strip()
    return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))

def _load_pub_key(key_input: Union[bytes, str, Ed25519PublicKey], allow_path: bool = True) -> Ed25519PublicKey:
    """Loads an Ed25519PublicKey from an object, raw bytes, hex string, PEM, or base64.
    ``allow_path=False`` for card-supplied input, which must never name a file to read."""
    if isinstance(key_input, Ed25519PublicKey):
        return key_input
    if isinstance(key_input, str):
        key_str = key_input.strip()
        if key_str.startswith("-----BEGIN"):
            return serialization.load_pem_public_key(key_str.encode("utf-8"))  # type: ignore[return-value]
        if allow_path and os.path.isfile(key_str):
            with open(key_str, "rb") as f:
                content = f.read().strip()
            if content.startswith(b"-----BEGIN"):
                return serialization.load_pem_public_key(content)  # type: ignore[return-value]
            return _load_pub_key(content)
        if len(key_str) == 64:
            try:
                return Ed25519PublicKey.from_public_bytes(bytes.fromhex(key_str))
            except ValueError:
                pass
        try:
            return Ed25519PublicKey.from_public_bytes(_b64u_decode(key_str))
        except Exception:
            pass
    elif isinstance(key_input, bytes):
        if key_input.startswith(b"-----BEGIN"):
            return serialization.load_pem_public_key(key_input)  # type: ignore[return-value]
        if len(key_input) == 32:
            return Ed25519PublicKey.from_public_bytes(key_input)
        if len(key_input) == 64:
            try:
                return Ed25519PublicKey.from_public_bytes(bytes.fromhex(key_input.decode("ascii")))
            except Exception:
                pass
    raise ValueError(f"Unsupported public key format or invalid Ed25519 key: {key_input!r}")

def _load_priv_key(key_input: Union[bytes, str, Ed25519PrivateKey]) -> Ed25519PrivateKey:
    """Loads an Ed25519PrivateKey from an object, raw 32-byte seed, hex string, PEM, or file."""
    if isinstance(key_input, Ed25519PrivateKey):
        return key_input
    if isinstance(key_input, str):
        key_str = key_input.strip()
        if key_str.startswith("-----BEGIN"):
            return serialization.load_pem_private_key(key_str.encode("utf-8"), password=None)  # type: ignore[return-value]
        if os.path.isfile(key_str):
            with open(key_str, "rb") as f:
                content = f.read().strip()
            if content.startswith(b"-----BEGIN"):
                return serialization.load_pem_private_key(content, password=None)  # type: ignore[return-value]
            return _load_priv_key(content)
        if len(key_str) == 64:
            try:
                return Ed25519PrivateKey.from_private_bytes(bytes.fromhex(key_str))
            except ValueError:
                pass
        try:
            return Ed25519PrivateKey.from_private_bytes(_b64u_decode(key_str))
        except Exception:
            pass
    elif isinstance(key_input, bytes):
        if key_input.startswith(b"-----BEGIN"):
            return serialization.load_pem_private_key(key_input, password=None)  # type: ignore[return-value]
        if len(key_input) == 32:
            return Ed25519PrivateKey.from_private_bytes(key_input)
        if len(key_input) == 64:
            try:
                return Ed25519PrivateKey.from_private_bytes(bytes.fromhex(key_input.decode("ascii")))
            except Exception:
                pass
    raise ValueError(f"Unsupported private key format or invalid Ed25519 seed: {key_input!r}")

class A2AAuthenticator:
    """
    Mutual capability exchange and cryptographic attestation authenticator for A2A nodes.

    Provides Ed25519 keypair generation, AgentCard creation with RFC-8785 canonical signing,
    clock-skew resilient verification, and capability negotiation.
    """

    def __init__(
        self,
        private_key: Union[Ed25519PrivateKey, bytes, str, None] = None,
        public_key: Union[Ed25519PublicKey, bytes, str, None] = None,
        node_id: int = 1,
    ) -> None:
        self.node_id = int(node_id)
        if private_key is not None:
            self._private_key: Optional[Ed25519PrivateKey] = _load_priv_key(private_key)
            self._public_key: Ed25519PublicKey = self._private_key.public_key()
        elif public_key is not None:
            self._private_key = None
            self._public_key = _load_pub_key(public_key)
        else:
            self._private_key = Ed25519PrivateKey.generate()
            self._public_key = self._private_key.public_key()

    @property
    def private_key(self) -> Optional[Ed25519PrivateKey]:
        return self._private_key

    @property
    def public_key(self) -> Ed25519PublicKey:
        return self._public_key

    @property
    def public_key_bytes(self) -> bytes:
        return self._public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )

    @property
    def public_key_hex(self) -> str:
        return self.public_key_bytes.hex()

    @property
    def private_key_bytes(self) -> Optional[bytes]:
        if self._private_key is None:
            return None
        return self._private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )

    @property
    def private_key_hex(self) -> Optional[str]:
        b = self.private_key_bytes
        return b.hex() if b else None

    @classmethod
    def generate_keypair(cls, node_id: int = 1) -> A2AAuthenticator:
        """Generates a fresh Ed25519 keypair for the specified node ID."""
        return cls(private_key=Ed25519PrivateKey.generate(), node_id=node_id)

    @classmethod
    def from_private_key(cls, private_key: Union[bytes, str, Ed25519PrivateKey], node_id: int = 1) -> A2AAuthenticator:
        return cls(private_key=private_key, node_id=node_id)

    @classmethod
    def from_public_key(cls, public_key: Union[bytes, str, Ed25519PublicKey], node_id: int = 1) -> A2AAuthenticator:
        return cls(public_key=public_key, node_id=node_id)

    def create_card(
        self,
        agent_name: str,
        capabilities: List[str],
        endpoints: Optional[Dict[str, Any]] = None,
        ttl_seconds: int = 3600,
        node_id: Optional[int] = None,
        issued_at: Optional[int] = None,
        nonce: Optional[str] = None,
    ) -> dict:
        """
        Creates and signs an AgentCard with cryptographic proof of authenticity.

        Payload contains: agent_name, node_id, capabilities, endpoints, issued_at,
        expires_at, nonce, and public_key. The detached signature 'sig' is generated over
        the RFC-8785 canonical JSON bytes.
        """
        if self._private_key is None:
            raise ValueError("Cannot create a signed AgentCard without a private key")

        now = int(time.time()) if issued_at is None else int(issued_at)
        exp = now + int(ttl_seconds)
        nid = self.node_id if node_id is None else int(node_id)
        non = secrets.token_hex(16) if nonce is None else str(nonce)

        payload: Dict[str, Any] = {
            "agent_name": str(agent_name),
            "capabilities": sorted(list(capabilities)),
            "endpoints": dict(endpoints) if endpoints is not None else {"rpc": f"http://127.0.0.1:8700/a2a"},
            "expires_at": exp,
            "issued_at": now,
            "node_id": nid,
            "nonce": non,
            "public_key": self.public_key_hex,
        }

        payload_bytes = canonical_json(payload)
        sig_bytes = self._private_key.sign(payload_bytes)
        card = dict(payload)
        card["sig"] = sig_bytes.hex()
        return card

    @staticmethod
    def verify_card(
        card: dict,
        trusted_public_key: Union[bytes, str, Ed25519PublicKey, None] = None,
        max_clock_skew: int = 60,
        now_ts: Optional[float] = None,
    ) -> bool:
        """
        Validates AgentCard authenticity, signature, timestamps, and clock skew.

        Returns True only if the cryptographic signature is valid over the canonical payload
        under ``trusted_public_key`` (resolved by the caller from provisioning -- see
        :func:`resolve_trusted_key`), timestamps are within acceptable clock skew bounds,
        and expiration has not occurred. With no trusted key the card is unverified: False.
        """
        if not isinstance(card, dict):
            return False

        sig_raw = card.get("sig") or card.get("signature")
        if not sig_raw and isinstance(card.get("signatures"), list) and card["signatures"]:
            first_sig = card["signatures"][0]
            if isinstance(first_sig, dict):
                sig_raw = first_sig.get("signature") or first_sig.get("sig")
            elif isinstance(first_sig, str):
                sig_raw = first_sig

        if not sig_raw:
            return False

        try:
            if isinstance(sig_raw, bytes):
                sig_bytes = sig_raw
            elif isinstance(sig_raw, str):
                s_str = sig_raw.strip()
                if len(s_str) == 128:
                    sig_bytes = bytes.fromhex(s_str)
                else:
                    try:
                        sig_bytes = bytes.fromhex(s_str)
                    except ValueError:
                        sig_bytes = _b64u_decode(s_str)
            else:
                return False

            if len(sig_bytes) != 64:
                return False
        except Exception:
            return False

        issued_at = card.get("issued_at")
        expires_at = card.get("expires_at")
        if issued_at is None or expires_at is None:
            return False

        try:
            iat = float(issued_at)
            exp = float(expires_at)
        except (TypeError, ValueError):
            return False

        now = float(now_ts if now_ts is not None else time.time())
        skew = max(0, int(max_clock_skew))

        if iat > (now + skew):
            return False
        if exp < (now - skew):
            return False
        if exp <= iat:
            return False

        # Fail closed without a trusted key. The key a card embeds is the
        # signer's own claim: anyone can sign a card with a fresh key and embed
        # it, so it is only ever compared against the trusted key, never used.
        if trusted_public_key is None:
            return False
        try:
            pub_key = _load_pub_key(trusted_public_key)
            card_pub = card.get("public_key")
            if card_pub is not None:
                card_pub_key = _load_pub_key(card_pub, allow_path=False)
                if (
                    card_pub_key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
                    != pub_key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
                ):
                    return False
        except Exception:
            return False

        payload = {k: v for k, v in card.items() if k not in ("sig", "signature", "signatures")}
        payload_bytes = canonical_json(payload)

        try:
            pub_key.verify(sig_bytes, payload_bytes)
            return True
        except (InvalidSignature, Exception):
            return False

    @staticmethod
    def negotiate_capabilities(
        client_card: dict,
        required_capabilities: List[str],
        trusted_key: Union[bytes, str, Ed25519PublicKey, None] = None,
        max_clock_skew: int = 60,
    ) -> Tuple[bool, List[str]]:
        """
        Attests client card authenticity and negotiates mutual capabilities.

        Returns (True, granted_capabilities) if the card is authentic and all required
        capabilities are present; returns (False, missing_capabilities) otherwise.
        """
        is_valid = A2AAuthenticator.verify_card(
            client_card,
            trusted_public_key=trusted_key,
            max_clock_skew=max_clock_skew,
        )
        if not is_valid:
            return False, []

        card_caps = list(client_card.get("capabilities", []))
        card_set = set(card_caps)
        req_list = list(required_capabilities)
        req_set = set(req_list)

        if req_set.issubset(card_set):
            granted = [c for c in req_list if c in card_set]
            return True, granted
        else:
            missing = [c for c in req_list if c not in card_set]
            return False, missing

def verify_card(
    card: dict,
    trusted_public_key: Union[bytes, str, Ed25519PublicKey, None] = None,
    max_clock_skew: int = 60,
    now_ts: Optional[float] = None,
) -> bool:
    """Module-level convenience wrapper for A2AAuthenticator.verify_card."""
    return A2AAuthenticator.verify_card(
        card=card,
        trusted_public_key=trusted_public_key,
        max_clock_skew=max_clock_skew,
        now_ts=now_ts,
    )

def negotiate_capabilities(
    client_card: dict,
    required_capabilities: List[str],
    trusted_key: Union[bytes, str, Ed25519PublicKey, None] = None,
    max_clock_skew: int = 60,
) -> Tuple[bool, List[str]]:
    """Module-level convenience wrapper for A2AAuthenticator.negotiate_capabilities."""
    return A2AAuthenticator.negotiate_capabilities(
        client_card=client_card,
        required_capabilities=required_capabilities,
        trusted_key=trusted_key,
        max_clock_skew=max_clock_skew,
    )

_AGENT_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]*")


def resolve_trusted_key(agent_id: str) -> Optional[Ed25519PublicKey]:
    """The provisioned passport public key of a REGISTERED identity, or None.

    The same trust anchor the AgentCard projection binds its signing key to:
    ``[passport].key_dir/<agent_id>/public.key`` from the mios.toml SSOT, written
    by mios-passport and served by the shared ``_passport_load_public`` resolver.
    The identity is the operator's choice (never the card's claim). None -- no
    passport, or nothing provisioned for that identity -- means unverified.
    """
    if not _AGENT_ID_RE.fullmatch(agent_id or "") or ".." in agent_id:
        raise ValueError(f"Invalid agent identifier: {agent_id!r}")
    lib = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "lib", "mios"))
    for path in (os.path.join(lib, "agent-pipe"), lib):
        if path not in sys.path:
            sys.path.insert(0, path)
    import mios_toml
    from mios_pipe.identity import principal

    passport = mios_toml.section(mios_toml.load_merged(), "passport")
    if not passport.get("enable") or not passport.get("key_dir"):
        return None
    principal.configure(passport_key_dir=str(passport["key_dir"]))
    key = principal._passport_load_public(agent_id)
    return key if isinstance(key, Ed25519PublicKey) else None


def _cli_trusted_key(args: argparse.Namespace) -> Tuple[Any, str]:
    """(trusted key, "") from --key (explicit) or --agent (provisioned identity);
    (None, reason) when there is none, so the card stays unverified."""
    if args.key:
        return args.key, ""
    if args.agent:
        try:
            key = resolve_trusted_key(args.agent)
        except ValueError as exc:
            return None, f"unverified: {exc}"
        if key is None:
            return None, f"unverified: no provisioned passport public key for {args.agent!r}"
        return key, ""
    return None, "unverified: no trusted key (pass --key or a provisioned --agent)"


def _parse_card_input(raw: str) -> dict:
    """Parses a card from a raw JSON string or file path."""
    raw_str = raw.strip()
    if os.path.isfile(raw_str):
        with open(raw_str, "r", encoding="utf-8") as f:
            return json.load(f)
    return json.loads(raw_str)

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="MiOS A2A Cryptographic Attestation and Capability Engine")
    subparsers = parser.add_subparsers(dest="action", help="Action to perform")

    # Keygen
    p_keygen = subparsers.add_parser("keygen", help="Generate Ed25519 keypair")
    p_keygen.add_argument("--node-id", type=int, default=1, help="Node ID (default: 1)")
    p_keygen.add_argument("--out-priv", type=str, help="Output path for private key (hex)")
    p_keygen.add_argument("--out-pub", type=str, help="Output path for public key (hex)")

    # Sign card
    p_sign = subparsers.add_parser("sign-card", help="Create and sign an AgentCard")
    p_sign.add_argument("--agent", required=True, help="Agent name")
    p_sign.add_argument("--capabilities", required=True, help="Comma-separated capability list")
    p_sign.add_argument("--key", type=str, help="Private key (hex or file path)")
    p_sign.add_argument("--node-id", type=int, default=1, help="Node ID (default: 1)")
    p_sign.add_argument("--ttl", type=int, default=3600, help="Card TTL in seconds (default: 3600)")
    p_sign.add_argument("--endpoints", type=str, help="JSON endpoints dictionary")
    p_sign.add_argument("--out", type=str, help="Output file path for card JSON")

    # Verify card
    p_verify = subparsers.add_parser("verify-card", help="Verify an AgentCard")
    p_verify.add_argument("--card", required=True, help="Card JSON string or path to JSON file")
    p_verify.add_argument("--key", type=str, help="Trusted public key (hex or file path)")
    p_verify.add_argument("--agent", type=str, help="Trust the provisioned passport key of this identity")
    p_verify.add_argument("--clock-skew", type=int, default=60, help="Max clock skew seconds (default: 60)")

    # Negotiate
    p_neg = subparsers.add_parser("negotiate", help="Negotiate capabilities against an AgentCard")
    p_neg.add_argument("--card", required=True, help="Card JSON string or path to JSON file")
    p_neg.add_argument("--required", required=True, help="Comma-separated required capabilities")
    p_neg.add_argument("--key", type=str, help="Trusted public key (hex or file path)")
    p_neg.add_argument("--agent", type=str, help="Trust the provisioned passport key of this identity")
    p_neg.add_argument("--clock-skew", type=int, default=60, help="Max clock skew seconds (default: 60)")

    args = parser.parse_args(argv)
    if not args.action:
        parser.print_help()
        return 1

    if args.action == "keygen":
        auth = A2AAuthenticator.generate_keypair(node_id=args.node_id)
        res = {
            "node_id": auth.node_id,
            "private_key": auth.private_key_hex,
            "public_key": auth.public_key_hex,
        }
        if args.out_priv:
            with open(args.out_priv, "w", encoding="utf-8") as f:
                f.write(auth.private_key_hex or "")
        if args.out_pub:
            with open(args.out_pub, "w", encoding="utf-8") as f:
                f.write(auth.public_key_hex)
        print(json.dumps(res, indent=2))
        return 0

    elif args.action == "sign-card":
        auth = A2AAuthenticator.from_private_key(args.key, node_id=args.node_id) if args.key else A2AAuthenticator.generate_keypair(node_id=args.node_id)
        caps = [c.strip() for c in args.capabilities.split(",") if c.strip()]
        endpoints = json.loads(args.endpoints) if args.endpoints else None
        card = auth.create_card(
            agent_name=args.agent,
            capabilities=caps,
            endpoints=endpoints,
            ttl_seconds=args.ttl,
            node_id=args.node_id,
        )
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(card, f, indent=2)
        print(json.dumps(card, indent=2))
        return 0

    elif args.action == "verify-card":
        try:
            card = _parse_card_input(args.card)
        except Exception as e:
            print(json.dumps({"valid": False, "error": f"Invalid card input: {e}"}))
            return 1
        trusted, why = _cli_trusted_key(args)
        if trusted is None:
            print(json.dumps({"valid": False, "error": why}))
            return 1
        valid = A2AAuthenticator.verify_card(card, trusted_public_key=trusted, max_clock_skew=args.clock_skew)
        print(json.dumps({"valid": valid}))
        return 0 if valid else 1

    elif args.action == "negotiate":
        try:
            card = _parse_card_input(args.card)
        except Exception as e:
            print(json.dumps({"authenticated": False, "error": f"Invalid card input: {e}"}))
            return 1
        reqs = [c.strip() for c in args.required.split(",") if c.strip()]
        trusted, why = _cli_trusted_key(args)
        if trusted is None:
            print(json.dumps({"authenticated": False, "error": why}))
            return 1
        ok, caps = A2AAuthenticator.negotiate_capabilities(card, required_capabilities=reqs, trusted_key=trusted, max_clock_skew=args.clock_skew)
        print(json.dumps({"authenticated": ok, "granted" if ok else "missing": caps}))
        return 0 if ok else 1

    return 0

if __name__ == "__main__":
    sys.exit(main())
