#!/usr/bin/env python3
# AI-hint: Ed25519 mutual authentication, X25519 ECDH key exchange, HKDF-SHA256 key derivation, and ChaCha20-Poly1305 AEAD wire encryption.
# AI-related: src/mios-rs/mios-node/src/crypto.rs, usr/libexec/mios/node/wire.py, tests/test-node-mesh.py
# AI-doc: usr/share/doc/mios/manual/node.md
"""
MiOS Node Cryptographic Handshake & Wire AEAD Encryption Engine (task_332 / task_2565).
Provides mutual Ed25519 identity verification, X25519 ephemeral Diffie-Hellman key exchange,
HKDF-SHA256 session key derivation, and ChaCha20-Poly1305 authenticated symmetric payload encryption.
"""

from __future__ import annotations

import os
import struct
import sys
from typing import Optional, Tuple

from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ed25519, x25519
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

# Import wire module
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import wire

class NodeIdentity:
    """Ed25519 signing identity keypair for edge node authentication."""

    def __init__(self, node_id: int, private_key: ed25519.Ed25519PrivateKey) -> None:
        self.node_id = node_id
        self._private_key = private_key
        self._public_key = private_key.public_key()

    @classmethod
    def generate(cls, node_id: int) -> NodeIdentity:
        return cls(node_id=node_id, private_key=ed25519.Ed25519PrivateKey.generate())

    @classmethod
    def from_private_bytes(cls, node_id: int, private_bytes: bytes) -> NodeIdentity:
        priv = ed25519.Ed25519PrivateKey.from_private_bytes(private_bytes)
        return cls(node_id=node_id, private_key=priv)

    @property
    def public_bytes(self) -> bytes:
        from cryptography.hazmat.primitives import serialization
        return self._public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )

    @property
    def private_bytes(self) -> bytes:
        from cryptography.hazmat.primitives import serialization
        return self._private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )

    def sign(self, message: bytes) -> bytes:
        return self._private_key.sign(message)

    @staticmethod
    def verify(public_bytes: bytes, message: bytes, signature: bytes) -> bool:
        try:
            pub = ed25519.Ed25519PublicKey.from_public_bytes(public_bytes)
            pub.verify(signature, message)
            return True
        except (InvalidSignature, Exception):
            return False

class HandshakeInitPacket:
    """Initiator mutual handshake message carrying Ed25519 identity pubkey and signed X25519 ephemeral pubkey."""

    def __init__(
        self,
        sender_node_id: int,
        id_pubkey: bytes,
        ephemeral_pubkey: bytes,
        signature: bytes,
    ) -> None:
        self.sender_node_id = sender_node_id
        self.id_pubkey = id_pubkey
        self.ephemeral_pubkey = ephemeral_pubkey
        self.signature = signature

    def to_dict(self) -> dict:
        return {
            "sender_node_id": self.sender_node_id,
            "id_pubkey": self.id_pubkey.hex(),
            "ephemeral_pubkey": self.ephemeral_pubkey.hex(),
            "signature": self.signature.hex(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> HandshakeInitPacket:
        return cls(
            sender_node_id=d["sender_node_id"],
            id_pubkey=bytes.fromhex(d["id_pubkey"]),
            ephemeral_pubkey=bytes.fromhex(d["ephemeral_pubkey"]),
            signature=bytes.fromhex(d["signature"]),
        )

class HandshakeRespPacket:
    """Responder mutual handshake message carrying Ed25519 identity pubkey and signed X25519 ephemeral pubkey."""

    def __init__(
        self,
        sender_node_id: int,
        id_pubkey: bytes,
        ephemeral_pubkey: bytes,
        signature: bytes,
    ) -> None:
        self.sender_node_id = sender_node_id
        self.id_pubkey = id_pubkey
        self.ephemeral_pubkey = ephemeral_pubkey
        self.signature = signature

    def to_dict(self) -> dict:
        return {
            "sender_node_id": self.sender_node_id,
            "id_pubkey": self.id_pubkey.hex(),
            "ephemeral_pubkey": self.ephemeral_pubkey.hex(),
            "signature": self.signature.hex(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> HandshakeRespPacket:
        return cls(
            sender_node_id=d["sender_node_id"],
            id_pubkey=bytes.fromhex(d["id_pubkey"]),
            ephemeral_pubkey=bytes.fromhex(d["ephemeral_pubkey"]),
            signature=bytes.fromhex(d["signature"]),
        )

class NodeCryptoSession:
    """Authenticated symmetric encryption session for bidirectional frame transmission."""

    def __init__(
        self,
        local_node_id: int,
        remote_node_id: int,
        tx_key: bytes,
        rx_key: bytes,
    ) -> None:
        self.local_node_id = local_node_id
        self.remote_node_id = remote_node_id
        self.tx_key = tx_key
        self.rx_key = rx_key
        self.tx_nonce = 0
        self.rx_nonce = 0
        self._tx_cipher = ChaCha20Poly1305(tx_key)
        self._rx_cipher = ChaCha20Poly1305(rx_key)

    def _make_nonce(self, counter: int, node_id: int) -> bytes:
        # 12-byte nonce: 8-byte counter (LE) + 4-byte node_id (BE)
        return struct.pack("<QI", counter, node_id)

    def encrypt_payload(self, plaintext: bytes) -> bytes:
        nonce = self._make_nonce(self.tx_nonce, self.local_node_id)
        self.tx_nonce += 1
        aad = struct.pack(">I", self.local_node_id)
        return self._tx_cipher.encrypt(nonce, plaintext, aad)

    def decrypt_payload(self, ciphertext_with_tag: bytes) -> bytes:
        nonce = self._make_nonce(self.rx_nonce, self.remote_node_id)
        self.rx_nonce += 1
        aad = struct.pack(">I", self.remote_node_id)
        try:
            return self._rx_cipher.decrypt(nonce, ciphertext_with_tag, aad)
        except InvalidTag as e:
            raise ValueError("ChaCha20-Poly1305 MAC verification failed - payload tampered") from e

    def encrypt_frame(self, frame: wire.Frame) -> wire.Frame:
        encrypted_payload = self.encrypt_payload(frame.payload)
        return wire.Frame.create(
            opcode=frame.header.opcode,
            node_id=self.local_node_id,
            payload=encrypted_payload,
        )

    def decrypt_frame(self, encrypted_frame: wire.Frame) -> wire.Frame:
        decrypted_payload = self.decrypt_payload(encrypted_frame.payload)
        return wire.Frame.create(
            opcode=encrypted_frame.header.opcode,
            node_id=encrypted_frame.header.node_id,
            payload=decrypted_payload,
        )

class CryptoHandshake:
    """Orchestrates mutual cryptographic handshakes between edge nodes."""

    @staticmethod
    def _generate_x25519_keypair(ephemeral_priv_bytes: Optional[bytes] = None) -> Tuple[x25519.X25519PrivateKey, bytes]:
        from cryptography.hazmat.primitives import serialization
        if ephemeral_priv_bytes:
            priv = x25519.X25519PrivateKey.from_private_bytes(ephemeral_priv_bytes)
        else:
            priv = x25519.X25519PrivateKey.generate()
        pub_bytes = priv.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return priv, pub_bytes

    @classmethod
    def create_init(
        cls,
        identity: NodeIdentity,
        ephemeral_priv_bytes: Optional[bytes] = None,
    ) -> Tuple[HandshakeInitPacket, bytes]:
        from cryptography.hazmat.primitives import serialization
        priv, pub_bytes = cls._generate_x25519_keypair(ephemeral_priv_bytes)
        sig = identity.sign(pub_bytes)

        init_packet = HandshakeInitPacket(
            sender_node_id=identity.node_id,
            id_pubkey=identity.public_bytes,
            ephemeral_pubkey=pub_bytes,
            signature=sig,
        )
        priv_raw = priv.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        return init_packet, priv_raw

    @classmethod
    def process_init_and_respond(
        cls,
        identity: NodeIdentity,
        init: HandshakeInitPacket,
        ephemeral_priv_bytes: Optional[bytes] = None,
    ) -> Tuple[HandshakeRespPacket, NodeCryptoSession]:
        # 1. Verify initiator's Ed25519 signature over their ephemeral pubkey
        if not NodeIdentity.verify(init.id_pubkey, init.ephemeral_pubkey, init.signature):
            raise ValueError("Initiator Ed25519 signature verification failed")

        # 2. Generate responder ephemeral keypair & compute shared secret
        resp_priv, resp_pub_bytes = cls._generate_x25519_keypair(ephemeral_priv_bytes)
        init_pub = x25519.X25519PublicKey.from_public_bytes(init.ephemeral_pubkey)
        shared_secret = resp_priv.exchange(init_pub)

        # 3. Derive symmetric session keys via HKDF-SHA256
        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=64,
            salt=None,
            info=b"mios-mesh-wire-v1-session-keys",
        )
        derived_keys = hkdf.derive(shared_secret)
        k1 = derived_keys[0:32]
        k2 = derived_keys[32:64]

        # Responder transmits with k2, receives with k1
        session = NodeCryptoSession(
            local_node_id=identity.node_id,
            remote_node_id=init.sender_node_id,
            tx_key=k2,
            rx_key=k1,
        )

        # 4. Sign own ephemeral pubkey
        sig = identity.sign(resp_pub_bytes)
        resp_packet = HandshakeRespPacket(
            sender_node_id=identity.node_id,
            id_pubkey=identity.public_bytes,
            ephemeral_pubkey=resp_pub_bytes,
            signature=sig,
        )

        return resp_packet, session

    @classmethod
    def finalize_init(
        cls,
        identity: NodeIdentity,
        ephemeral_priv_bytes: bytes,
        resp: HandshakeRespPacket,
    ) -> NodeCryptoSession:
        # 1. Verify responder's Ed25519 signature over their ephemeral pubkey
        if not NodeIdentity.verify(resp.id_pubkey, resp.ephemeral_pubkey, resp.signature):
            raise ValueError("Responder Ed25519 signature verification failed")

        # 2. Compute shared secret
        init_priv = x25519.X25519PrivateKey.from_private_bytes(ephemeral_priv_bytes)
        resp_pub = x25519.X25519PublicKey.from_public_bytes(resp.ephemeral_pubkey)
        shared_secret = init_priv.exchange(resp_pub)

        # 3. Derive symmetric session keys
        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=64,
            salt=None,
            info=b"mios-mesh-wire-v1-session-keys",
        )
        derived_keys = hkdf.derive(shared_secret)
        k1 = derived_keys[0:32]
        k2 = derived_keys[32:64]

        # Initiator transmits with k1, receives with k2
        return NodeCryptoSession(
            local_node_id=identity.node_id,
            remote_node_id=resp.sender_node_id,
            tx_key=k1,
            rx_key=k2,
        )

def main() -> int:
    print("[crypto.py] MiOS Node Cryptographic Handshake & Wire AEAD Ready.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
