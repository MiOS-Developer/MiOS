#!/usr/bin/env python3
# AI-hint: 16-byte fixed binary wire protocol encoder, decoder, async stream codec, and opcode dispatcher for mios-node.
# AI-related: src/mios-rs/mios-node/src/wire.rs, tests/test-node-mesh.py
# AI-doc: usr/share/doc/mios/manual/node.md
"""
WS-NODE: Edge Micro-Mesh 16-Byte Fixed Binary Wire Protocol & Async TCP Stream Engine.
Provides byte-for-byte wire compatibility with the Rust mios-node runtime (task_330 / task_2563).

Header Format (16 Bytes Fixed, Big-Endian Network Byte Order):
+-------------------------------------------------------------------+
| Magic (2B: 0x4D 0x49) | Ver (1B) | Opcode (1B) | NodeID (4B: u32) |
+-------------------------------------------------------------------+
| PayloadLen (4B: u32)             | Checksum (4B: u32 CRC32)       |
+-------------------------------------------------------------------+
"""

from __future__ import annotations

import asyncio
import enum
import struct
import sys
import zlib
from typing import Callable, Dict, List, Optional, Tuple, Union

MIOS_MAGIC = 0x4D49  # 'MI'
MIOS_VERSION = 0x01
HEADER_SIZE = 16
MAX_PAYLOAD_LEN = 64 * 1024 * 1024  # 64 MB ceiling
HEADER_STRUCT = struct.Struct(">HBBIII")  # Big-Endian: u16, u8, u8, u32, u32, u32

class Opcode(enum.IntEnum):
    HEARTBEAT = 0x01
    NODE_ANNOUNCE = 0x02
    TASK_OFFLOAD = 0x03
    TASK_RESULT = 0x04
    STATE_SYNC = 0x05
    STATE_ACK = 0x06
    ERROR = 0x07

# Alias for Rust MessageType parity
MessageType = Opcode

class Header:
    __slots__ = ("magic", "version", "opcode", "node_id", "payload_len", "checksum")

    def __init__(
        self,
        magic: int = MIOS_MAGIC,
        version: int = MIOS_VERSION,
        opcode: Union[Opcode, int] = Opcode.HEARTBEAT,
        node_id: int = 0,
        payload_len: int = 0,
        checksum: int = 0,
    ):
        self.magic = magic
        self.version = version
        self.opcode = Opcode(opcode)
        self.node_id = node_id
        self.payload_len = payload_len
        self.checksum = checksum

    @property
    def msg_type(self) -> Opcode:
        return self.opcode

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Header):
            return False
        return (
            self.magic == other.magic
            and self.version == other.version
            and self.opcode == other.opcode
            and self.node_id == other.node_id
            and self.payload_len == other.payload_len
            and self.checksum == other.checksum
        )

    def __repr__(self) -> str:
        return (
            f"Header(magic=0x{self.magic:04X}, version={self.version}, opcode={self.opcode.name}, "
            f"node_id={self.node_id}, payload_len={self.payload_len}, checksum=0x{self.checksum:08X})"
        )

    def encode(self) -> bytes:
        return HEADER_STRUCT.pack(
            self.magic,
            self.version,
            int(self.opcode),
            self.node_id,
            self.payload_len,
            self.checksum,
        )

    @classmethod
    def decode(cls, data: bytes) -> Header:
        if len(data) < HEADER_SIZE:
            raise ValueError(f"Buffer underflow: expected {HEADER_SIZE} bytes, got {len(data)}")
        magic, version, opcode_val, node_id, payload_len, checksum = HEADER_STRUCT.unpack_from(data, 0)
        if magic != MIOS_MAGIC:
            raise ValueError(f"Invalid MiOS magic: 0x{magic:04X} (expected 0x{MIOS_MAGIC:04X})")
        if version != MIOS_VERSION:
            raise ValueError(f"Unsupported protocol version: {version} (expected {MIOS_VERSION})")
        try:
            opcode = Opcode(opcode_val)
        except ValueError:
            raise ValueError(f"Unknown MiOS message opcode: 0x{opcode_val:02X}")
        if payload_len > MAX_PAYLOAD_LEN:
            raise ValueError(f"Payload length exceeds maximum allowed ceiling: {payload_len} > {MAX_PAYLOAD_LEN}")
        return cls(
            magic=magic,
            version=version,
            opcode=opcode,
            node_id=node_id,
            payload_len=payload_len,
            checksum=checksum,
        )

WireHeader = Header

class Frame:
    __slots__ = ("header", "payload")

    def __init__(self, header: Header, payload: bytes):
        self.header = header
        self.payload = bytes(payload)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Frame):
            return False
        return self.header == other.header and self.payload == other.payload

    def __repr__(self) -> str:
        return f"Frame(header={self.header!r}, payload={len(self.payload)}B)"

    @classmethod
    def create(cls, opcode: Union[Opcode, int], node_id: int, payload: bytes) -> Frame:
        op = Opcode(opcode)
        payload_bytes = bytes(payload)
        crc = zlib.crc32(payload_bytes) & 0xFFFFFFFF
        header = Header(
            magic=MIOS_MAGIC,
            version=MIOS_VERSION,
            opcode=op,
            node_id=node_id,
            payload_len=len(payload_bytes),
            checksum=crc,
        )
        return cls(header=header, payload=payload_bytes)

    @classmethod
    def new(cls, opcode: Union[Opcode, int], node_id: int, payload: bytes) -> Frame:
        return cls.create(opcode, node_id, payload)

    def encode(self) -> bytes:
        return self.header.encode() + self.payload

    @classmethod
    def decode(cls, data: bytes) -> Frame:
        header = Header.decode(data)
        expected_len = HEADER_SIZE + header.payload_len
        if len(data) < expected_len:
            raise ValueError(f"Incomplete payload: expected {header.payload_len}B, available {len(data) - HEADER_SIZE}B")
        payload = data[HEADER_SIZE:expected_len]
        actual_crc = zlib.crc32(payload) & 0xFFFFFFFF
        if actual_crc != header.checksum:
            raise ValueError(f"CRC32 mismatch: expected 0x{header.checksum:08X}, calculated 0x{actual_crc:08X}")
        return cls(header=header, payload=payload)

class WireFrame(Frame):
    def __init__(self, opcode: Union[Opcode, int], node_id: int, payload: bytes):
        fr = Frame.create(opcode, node_id, payload)
        super().__init__(header=fr.header, payload=fr.payload)

class AsyncFrameBuffer:
    """Stream buffer for non-blocking TCP streams; extracts complete frames as byte chunks arrive."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> None:
        self._buf.extend(data)

    def feed_data(self, data: bytes) -> None:
        self.feed(data)

    @property
    def buffer_len(self) -> int:
        return len(self._buf)

    def try_pop_frame(self) -> Optional[Frame]:
        if len(self._buf) < HEADER_SIZE:
            return None

        # Peek header
        magic, version, opcode_val, node_id, payload_len, checksum = HEADER_STRUCT.unpack_from(self._buf, 0)
        if magic != MIOS_MAGIC:
            raise ValueError(f"Invalid MiOS magic: 0x{magic:04X}")
        if version != MIOS_VERSION:
            raise ValueError(f"Unsupported protocol version: {version}")
        if payload_len > MAX_PAYLOAD_LEN:
            raise ValueError(f"Payload length exceeds maximum: {payload_len}")

        total_len = HEADER_SIZE + payload_len
        if len(self._buf) < total_len:
            # Need more data
            return None

        raw_frame = bytes(self._buf[:total_len])
        del self._buf[:total_len]
        return Frame.decode(raw_frame)

    def pop_all_frames(self) -> List[Frame]:
        frames = []
        while True:
            frame = self.try_pop_frame()
            if frame is None:
                break
            frames.append(frame)
        return frames

    def clear(self) -> None:
        self._buf.clear()

class AsyncFrameCodec:
    """Async TCP stream reader and writer using asyncio StreamReader / StreamWriter."""

    @staticmethod
    async def read_frame(reader: asyncio.StreamReader) -> Frame:
        header_bytes = await reader.readexactly(HEADER_SIZE)
        header = Header.decode(header_bytes)
        if header.payload_len > MAX_PAYLOAD_LEN:
            raise ValueError(f"Payload length {header.payload_len} exceeds ceiling {MAX_PAYLOAD_LEN}")
        payload = await reader.readexactly(header.payload_len) if header.payload_len > 0 else b""
        actual_crc = zlib.crc32(payload) & 0xFFFFFFFF
        if actual_crc != header.checksum:
            raise ValueError(f"CRC32 mismatch: expected 0x{header.checksum:08X}, calculated 0x{actual_crc:08X}")
        return Frame(header=header, payload=payload)

    @staticmethod
    async def write_frame(writer: asyncio.StreamWriter, frame: Frame) -> None:
        data = frame.encode()
        writer.write(data)
        await writer.drain()

class NodeWireDispatcher:
    """Dispatches received frames to registered opcode handlers."""

    def __init__(self, node_id: int = 0):
        self.node_id = node_id
        self.handlers: Dict[Opcode, Callable[[Frame], Optional[Frame]]] = {}

    def register_handler(self, opcode: Union[Opcode, int], handler: Callable[[Frame], Optional[Frame]]) -> None:
        self.handlers[Opcode(opcode)] = handler

    def register(self, opcode: Union[Opcode, int], handler: Callable[[Frame], Optional[Frame]]) -> None:
        self.register_handler(opcode, handler)

    def dispatch(self, frame: Frame) -> Optional[Frame]:
        handler = self.handlers.get(frame.header.opcode)
        if handler:
            return handler(frame)
        err_msg = f"Unhandled opcode 0x{int(frame.header.opcode):02X}".encode("utf-8")
        return Frame.create(Opcode.ERROR, self.node_id, err_msg)

    def process_packet(self, data: bytes) -> Optional[bytes]:
        frame = Frame.decode(data)
        resp_frame = self.dispatch(frame)
        if resp_frame:
            return resp_frame.encode()
        return None

OpcodeDispatcher = NodeWireDispatcher

class AsyncTcpFrameServer:
    """Async TCP server listening for framed connections and routing frames to a handler callback."""

    def __init__(
        self,
        node_id: int,
        host: str = "127.0.0.1",
        port: int = 0,
        handler: Optional[Callable[[Frame, asyncio.StreamWriter], Optional[Frame]]] = None,
    ) -> None:
        self.node_id = node_id
        self.host = host
        self.port = port
        self.handler = handler
        self.server: Optional[asyncio.Server] = None
        self.incoming_queue: asyncio.Queue[Tuple[Frame, str]] = asyncio.Queue()
        self._is_running = False

    async def start(self) -> int:
        self.server = await asyncio.start_server(self._handle_client, self.host, self.port)
        self._is_running = True
        sockets = self.server.sockets
        if sockets:
            self.port = sockets[0].getsockname()[1]
        return self.port

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        peer_str = f"{peer[0]}:{peer[1]}" if peer else "unknown"
        try:
            while self._is_running:
                try:
                    frame = await AsyncFrameCodec.read_frame(reader)
                except (asyncio.IncompleteReadError, ConnectionResetError, EOFError):
                    break
                except Exception as e:
                    break

                await self.incoming_queue.put((frame, peer_str))

                if self.handler:
                    try:
                        resp = self.handler(frame, writer)
                        if resp:
                            await AsyncFrameCodec.write_frame(writer, resp)
                    except Exception:
                        pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def stop(self) -> None:
        self._is_running = False
        if self.server:
            self.server.close()
            await self.server.wait_closed()

class AsyncTcpFrameClient:
    """Async TCP client for sending and receiving binary frames over a TCP connection."""

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None

    async def connect(self) -> None:
        self.reader, self.writer = await asyncio.open_connection(self.host, self.port)

    async def send_frame(self, frame: Frame) -> None:
        if not self.writer:
            raise ConnectionError("Client is not connected")
        await AsyncFrameCodec.write_frame(self.writer, frame)

    async def recv_frame(self) -> Frame:
        if not self.reader:
            raise ConnectionError("Client is not connected")
        return await AsyncFrameCodec.read_frame(self.reader)

    async def close(self) -> None:
        if self.writer:
            self.writer.close()
            try:
                await self.writer.wait_closed()
            except Exception:
                pass

def main() -> int:
    print("[wire.py] MiOS Async TCP Frame Engine Ready.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
