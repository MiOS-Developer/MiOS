#!/usr/bin/env python3
# AI-hint: Edge node capability advertising in Announce frames for mios-node (task_338 / task_2571).
# AI-related: usr/libexec/mios/node/wire.py, tests/test-node.py
"""
MiOS Edge Node Capability Advertising & Telemetry Engine.
Defines HardwareSpecs, VramTelemetry, EngineTiers, ActiveTransports, NodeCapabilities,
and CapabilityRegistry with Opcode 0x02 NodeAnnounce serialization.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import os
import platform
import sys
import threading
import time
from typing import Dict, List, Optional, Tuple

_NODE_DIR = os.path.dirname(os.path.abspath(__file__))
if _NODE_DIR not in sys.path:
    sys.path.insert(0, _NODE_DIR)

try:
    from wire import Frame, Header, MessageType
except ImportError:
    try:
        from usr.libexec.mios.node.wire import Frame, Header, MessageType
    except ImportError:
        from node.wire import Frame, Header, MessageType  # type: ignore

@dataclass
class HardwareSpecs:
    cpu_arch: str = platform.machine()
    cpu_cores: int = os.cpu_count() or 4
    cpu_frequency_mhz: int = 2400
    ram_total_kb: int = 8 * 1024 * 1024
    ram_available_kb: int = 4 * 1024 * 1024

@dataclass
class VramTelemetry:
    gpu_vendor: str = "None"  # "NVIDIA", "AMD", "Intel", "Apple", "None"
    gpu_model: Optional[str] = None
    vram_total_mb: int = 0
    vram_available_mb: int = 0
    has_npu: bool = False

@dataclass
class EngineTiers:
    wasm_tier: bool = True
    native_tier: bool = True
    llm_inference: bool = False
    supported_task_types: List[str] = field(
        default_factory=lambda: ["wasm", "native_elf", "crdt_sync"]
    )

@dataclass
class ActiveTransports:
    lan_broadcast: bool = True
    direct_tcp: bool = True
    tailscale: bool = False
    wireguard: bool = False
    ble_mesh: bool = False
    endpoints: List[str] = field(default_factory=lambda: ["127.0.0.1:8650"])

@dataclass
class NodeCapabilities:
    hardware: HardwareSpecs = field(default_factory=HardwareSpecs)
    vram: VramTelemetry = field(default_factory=VramTelemetry)
    engines: EngineTiers = field(default_factory=EngineTiers)
    transports: ActiveTransports = field(default_factory=ActiveTransports)
    has_gpio: bool = False
    has_i2c: bool = False

@dataclass
class NodeAnnouncePayload:
    node_id: int
    hostname: str
    capabilities: NodeCapabilities
    timestamp_utc: int = field(default_factory=lambda: int(time.time()))
    version: str = "0.3.0"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> NodeAnnouncePayload:
        caps_data = data.get("capabilities", {})
        caps = NodeCapabilities(
            hardware=HardwareSpecs(**caps_data.get("hardware", {})),
            vram=VramTelemetry(**caps_data.get("vram", {})),
            engines=EngineTiers(**caps_data.get("engines", {})),
            transports=ActiveTransports(**caps_data.get("transports", {})),
            has_gpio=caps_data.get("has_gpio", False),
            has_i2c=caps_data.get("has_i2c", False),
        )
        return cls(
            node_id=data["node_id"],
            hostname=data["hostname"],
            capabilities=caps,
            timestamp_utc=data.get("timestamp_utc", int(time.time())),
            version=data.get("version", "0.3.0"),
        )

    def to_frame(self) -> Frame:
        payload_bytes = json.dumps(self.to_dict()).encode("utf-8")
        return Frame.create(
            opcode=MessageType.NODE_ANNOUNCE,
            node_id=self.node_id,
            payload=payload_bytes,
        )

    @classmethod
    def from_frame(cls, frame: Frame) -> NodeAnnouncePayload:
        if frame.header.opcode != MessageType.NODE_ANNOUNCE:
            raise ValueError(
                f"Invalid message type for NodeAnnounce: {frame.header.opcode}"
            )
        data = json.loads(frame.payload.decode("utf-8"))
        return cls.from_dict(data)

def probe_node_capabilities() -> NodeCapabilities:
    """Probes system hardware, sysfs interfaces, memory, and devices."""
    caps = NodeCapabilities()

    # 1. Linux /proc/meminfo probe
    if os.path.exists("/proc/meminfo"):
        try:
            with open("/proc/meminfo", "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        parts = line.split()
                        if len(parts) >= 2 and parts[1].isdigit():
                            caps.hardware.ram_total_kb = int(parts[1])
                    elif line.startswith("MemAvailable:"):
                        parts = line.split()
                        if len(parts) >= 2 and parts[1].isdigit():
                            caps.hardware.ram_available_kb = int(parts[1])
        except Exception:
            pass

    # 2. GPIO & I2C probe
    caps.has_gpio = os.path.exists("/dev/gpiochip0") or os.path.exists("/sys/class/gpio")
    caps.has_i2c = os.path.exists("/dev/i2c-0") or os.path.exists("/dev/i2c-1")

    # 3. GPU probe
    if os.path.exists("/dev/nvidia0"):
        caps.vram.gpu_vendor = "NVIDIA"
        caps.vram.gpu_model = "NVIDIA GPU Accelerator"
        caps.vram.vram_total_mb = 8192
        caps.vram.vram_available_mb = 6144
        caps.engines.llm_inference = True
    elif os.path.exists("/sys/class/drm"):
        caps.vram.gpu_vendor = "Generic DRM"

    return caps

class CapabilityRegistry:
    """In-memory peer capability registry with query filters and stale eviction."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._peers: Dict[int, Tuple[NodeAnnouncePayload, float]] = {}

    def register_announce(
        self, payload: NodeAnnouncePayload, received_at: Optional[float] = None
    ) -> None:
        with self._lock:
            ts = received_at if received_at is not None else time.time()
            self._peers[payload.node_id] = (payload, ts)

    def get_capabilities(self, node_id: int) -> Optional[NodeCapabilities]:
        with self._lock:
            entry = self._peers.get(node_id)
            return entry[0].capabilities if entry else None

    def get_announce(self, node_id: int) -> Optional[NodeAnnouncePayload]:
        with self._lock:
            entry = self._peers.get(node_id)
            return entry[0] if entry else None

    def find_eligible_nodes(
        self,
        min_ram_kb: int = 0,
        min_vram_mb: int = 0,
        require_wasm: bool = False,
        require_native: bool = False,
        require_gpio: bool = False,
        require_i2c: bool = False,
    ) -> List[int]:
        candidates: List[int] = []
        with self._lock:
            for node_id, (payload, _) in self._peers.items():
                caps = payload.capabilities
                if caps.hardware.ram_available_kb < min_ram_kb:
                    continue
                if caps.vram.vram_available_mb < min_vram_mb:
                    continue
                if require_wasm and not caps.engines.wasm_tier:
                    continue
                if require_native and not caps.engines.native_tier:
                    continue
                if require_gpio and not caps.has_gpio:
                    continue
                if require_i2c and not caps.has_i2c:
                    continue
                candidates.append(node_id)

        candidates.sort()
        return candidates

    def evict_stale(self, max_age_secs: float, now: Optional[float] = None) -> int:
        current_time = now if now is not None else time.time()
        with self._lock:
            before_len = len(self._peers)
            self._peers = {
                nid: (p, ts)
                for nid, (p, ts) in self._peers.items()
                if (current_time - ts) <= max_age_secs
            }
            return before_len - len(self._peers)

    def active_node_count(self) -> int:
        with self._lock:
            return len(self._peers)
