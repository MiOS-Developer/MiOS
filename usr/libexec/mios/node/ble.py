#!/usr/bin/env python3
# AI-hint: BLE beaconing for offline local mesh bootstrap for mios-node (T-395 / AGY-1993).
# AI-related: usr/libexec/mios/node/crypto.py, tests/test-node.py
"""
MiOS BLE Beaconing & Offline Local Mesh Bootstrap Engine.
Implements GATT service/characteristic definitions for headless edge blades,
X25519 Diffie-Hellman key exchange, HKDF-SHA256 derivation,
ChaCha20-Poly1305 AEAD encrypted credential provisioning, and mockable hardware adapter.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import IntEnum
import hashlib
import json
import os
import struct
import sys
import threading
import time
from typing import Dict, Optional, Tuple

_NODE_DIR = os.path.dirname(os.path.abspath(__file__))
if _NODE_DIR not in sys.path:
    sys.path.insert(0, _NODE_DIR)

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import x25519
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

BLE_SERVICE_UUID = "4D494F53-0001-1000-8000-00805F9B34FB"
BLE_CHAR_IDENTITY_UUID = "4D494F53-0002-1000-8000-00805F9B34FB"
BLE_CHAR_ECDH_UUID = "4D494F53-0003-1000-8000-00805F9B34FB"
BLE_CHAR_PROVISION_UUID = "4D494F53-0004-1000-8000-00805F9B34FB"

BLE_HKDF_INFO = b"wifi-provisioning"
BLE_AEAD_AAD = b"mios-ble-v2"

class BleBootstrapState(IntEnum):
    UNPROVISIONED = 0
    HANDSHAKING = 1
    PROVISIONING = 2
    PROVISIONED = 3
    FAILED = 4

@dataclass
class ProvisioningPayload:
    ssid: str
    psk: str
    cluster_token: str
    coordinator_endpoint: str
    mesh_network_key: Optional[str] = None
    timestamp_utc: int = field(default_factory=lambda: int(time.time()))

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> ProvisioningPayload:
        return cls(
            ssid=d["ssid"],
            psk=d["psk"],
            cluster_token=d["cluster_token"],
            coordinator_endpoint=d["coordinator_endpoint"],
            mesh_network_key=d.get("mesh_network_key"),
            timestamp_utc=d.get("timestamp_utc", int(time.time())),
        )

class BleAdapter:
    """Interface for Bluetooth Low Energy GATT operations."""

    def start_advertising(self, service_uuid: str, node_id: int) -> None:
        raise NotImplementedError

    def stop_advertising(self) -> None:
        raise NotImplementedError

    def is_advertising(self) -> bool:
        raise NotImplementedError

    def set_characteristic_value(self, char_uuid: str, data: bytes) -> None:
        raise NotImplementedError

    def get_characteristic_value(self, char_uuid: str) -> bytes:
        raise NotImplementedError

class MockBleAdapter(BleAdapter):
    """In-memory mock BLE adapter for deterministic automated testing."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._advertising = False
        self._characteristics: Dict[str, bytes] = {}

    def start_advertising(self, service_uuid: str, node_id: int) -> None:
        with self._lock:
            self._advertising = True

    def stop_advertising(self) -> None:
        with self._lock:
            self._advertising = False

    def is_advertising(self) -> bool:
        with self._lock:
            return self._advertising

    def set_characteristic_value(self, char_uuid: str, data: bytes) -> None:
        with self._lock:
            self._characteristics[char_uuid] = bytes(data)

    def get_characteristic_value(self, char_uuid: str) -> bytes:
        with self._lock:
            if char_uuid not in self._characteristics:
                raise KeyError(f"Characteristic {char_uuid} not found")
            return self._characteristics[char_uuid]

class BleMeshBootstrap:
    """Manages BLE GATT beaconing, X25519 key exchange, and encrypted provisioning."""

    def __init__(self, node_id: int, adapter: BleAdapter) -> None:
        self.node_id = node_id
        self.adapter = adapter
        self._state = BleBootstrapState.UNPROVISIONED
        self._private_key = x25519.X25519PrivateKey.generate()
        self._public_key = self._private_key.public_key()
        self._shared_key: Optional[bytes] = None
        self._credentials: Optional[ProvisioningPayload] = None
        self._lock = threading.Lock()

    @property
    def public_bytes(self) -> bytes:
        return self._public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )

    @property
    def state(self) -> BleBootstrapState:
        with self._lock:
            return self._state

    def start(self) -> None:
        with self._lock:
            # 1. Identity characteristic: 4-byte node_id + 1-byte state
            id_val = struct.pack(">IB", self.node_id, BleBootstrapState.UNPROVISIONED)
            self.adapter.set_characteristic_value(BLE_CHAR_IDENTITY_UUID, id_val)

            # 2. ECDH characteristic: 32-byte X25519 public key
            self.adapter.set_characteristic_value(BLE_CHAR_ECDH_UUID, self.public_bytes)

            # 3. Start advertising
            self.adapter.start_advertising(BLE_SERVICE_UUID, self.node_id)
            self._state = BleBootstrapState.UNPROVISIONED

    def handle_ecdh_exchange(self, peer_public_bytes: bytes) -> None:
        if len(peer_public_bytes) != 32:
            raise ValueError(f"Invalid X25519 public key length: {len(peer_public_bytes)}")

        peer_pub = x25519.X25519PublicKey.from_public_bytes(peer_public_bytes)
        shared_secret = self._private_key.exchange(peer_pub)

        # Derive 32-byte session key via HKDF-SHA256
        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=hashlib.sha256(self.public_bytes + peer_public_bytes).digest(),
            info=BLE_HKDF_INFO,
        )
        self._shared_key = hkdf.derive(shared_secret)

        with self._lock:
            self._state = BleBootstrapState.HANDSHAKING
            id_val = struct.pack(">IB", self.node_id, BleBootstrapState.HANDSHAKING)
            self.adapter.set_characteristic_value(BLE_CHAR_IDENTITY_UUID, id_val)

    def handle_provisioning_write(self, encrypted_payload: bytes) -> ProvisioningPayload:
        if len(encrypted_payload) < 12 + 16:
            raise ValueError("Provisioning packet must contain a nonce and authentication tag")
        with self._lock:
            if self._shared_key is None:
                raise RuntimeError("ECDH handshake not completed or already consumed")
            aead = ChaCha20Poly1305(self._shared_key)
            decrypted_bytes = aead.decrypt(encrypted_payload[:12], encrypted_payload[12:], BLE_AEAD_AAD)
            creds = ProvisioningPayload.from_dict(json.loads(decrypted_bytes.decode("utf-8")))
            self._shared_key = None
            self._credentials = creds
            self._state = BleBootstrapState.PROVISIONED
            id_val = struct.pack(">IB", self.node_id, BleBootstrapState.PROVISIONED)
            self.adapter.set_characteristic_value(BLE_CHAR_IDENTITY_UUID, id_val)
            self.adapter.stop_advertising()
        return creds

    def get_credentials(self) -> Optional[ProvisioningPayload]:
        with self._lock:
            return self._credentials

def provision_remote_node(
    adapter: BleAdapter,
    payload: ProvisioningPayload,
) -> None:
    """Client provisioner: connects to BLE node, performs X25519 ECDH, encrypts, and writes credentials."""
    # 1. Read node identity and public key
    _ = adapter.get_characteristic_value(BLE_CHAR_IDENTITY_UUID)
    node_pub_bytes = adapter.get_characteristic_value(BLE_CHAR_ECDH_UUID)

    if len(node_pub_bytes) != 32:
        raise ValueError("Invalid node public key length")

    node_pub = x25519.X25519PublicKey.from_public_bytes(node_pub_bytes)

    # 2. Generate provisioner keypair
    prov_priv = x25519.X25519PrivateKey.generate()
    prov_pub = prov_priv.public_key()
    prov_pub_bytes = prov_pub.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )

    # 3. Write provisioner public key to Char 2
    adapter.set_characteristic_value(BLE_CHAR_ECDH_UUID, prov_pub_bytes)

    # 4. Compute shared secret and session key
    shared_secret = prov_priv.exchange(node_pub)
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=hashlib.sha256(node_pub_bytes + prov_pub_bytes).digest(),
        info=BLE_HKDF_INFO,
    )
    shared_key = hkdf.derive(shared_secret)

    # 5. Encrypt credentials with ChaCha20-Poly1305
    payload_json = json.dumps(payload.to_dict()).encode("utf-8")
    aead = ChaCha20Poly1305(shared_key)
    nonce = os.urandom(12)
    encrypted = nonce + aead.encrypt(nonce, payload_json, BLE_AEAD_AAD)

    # 6. Write encrypted credentials to Char 3
    adapter.set_characteristic_value(BLE_CHAR_PROVISION_UUID, encrypted)
