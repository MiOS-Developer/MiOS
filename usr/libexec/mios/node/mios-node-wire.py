#!/usr/bin/env python3
# AI-hint: 16-byte fixed binary wire protocol encoder, decoder, and opcode dispatcher for mios-node.
# AI-related: usr/share/doc/mios/adr/0020-edge-mesh-binary-wire-protocol-and-dual-tier-sandboxing.md, src/mios-rs/mios-node/src/wire.rs
# AI-doc: usr/share/doc/mios/manual/node.md
"""
WS-NODE: Edge Micro-Mesh 16-Byte Fixed Binary Wire Protocol Engine.
Provides byte-for-byte wire compatibility with the Rust mios-node runtime.

Header Format (16 Bytes Fixed, Big-Endian Network Byte Order):
+-------------------------------------------------------------------+
| Magic (2B: 0x4D 0x49) | Ver (1B) | Opcode (1B) | NodeID (4B: u32) |
+-------------------------------------------------------------------+
| PayloadLen (4B: u32)             | Checksum (4B: u32 CRC32)       |
+-------------------------------------------------------------------+
"""

import os
import sys

# Forward compatibility with wire.py
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import wire

# Re-export the protocol surface. This file is the FHS-named entry point and its
# docstring above advertises the encoder/decoder, but importing it only bound the
# name `wire`, so every attribute the header describes -- Frame included -- was
# missing from the module callers actually load.
from wire import (  # noqa: E402,F401 -- re-export
    HEADER_SIZE,
    HEADER_STRUCT,
    MAX_PAYLOAD_LEN,
    MIOS_MAGIC,
    MIOS_VERSION,
    AsyncFrameBuffer,
    AsyncFrameCodec,
    AsyncTcpFrameClient,
    AsyncTcpFrameServer,
    Frame,
    Header,
    NodeWireDispatcher,
    Opcode,
    WireFrame,
)

if __name__ == "__main__":
    sys.exit(wire.main())

