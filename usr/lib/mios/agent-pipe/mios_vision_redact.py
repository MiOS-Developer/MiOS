#!/usr/bin/env python3
# AI-hint: ATSPI accessibility tree sensitive widget coordinate detector and Wayland frame blur filter (task_483, task_2716).
# AI-doc: usr/share/doc/mios/manual/ch84-vision-redaction.md
"""ATSPI accessibility tree sensitive widget coordinate detector and Wayland frame blur filter.

Discovers on-screen sensitive/password input fields by querying the ATSPI / DBus accessibility
tree (looking for ROLE_PASSWORD_TEXT, ROLE_TEXT with STATE_PROTECTED, or security attributes).
Computes screen bounding boxes (x, y, width, height) in pixel coordinates.
Applies privacy redaction (Gaussian blur, box blur, or pixelation) to the bounding regions
on captured Wayland frame buffers / images.
Implements fallback pure-Python synthetic frame blurring when PIL / cv2 is not available or in synthetic mode.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import math
import os
import pathlib
import struct
import sys
import zlib
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

# Try importing PIL if available
try:
    from PIL import Image as PILImage
    from PIL import ImageFilter as PILImageFilter

    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False

# Try importing cv2 if available
try:
    import cv2  # type: ignore

    HAVE_CV2 = True
except ImportError:
    HAVE_CV2 = False

log = logging.getLogger("mios_vision_redact")

# ==============================================================================
# Configuration & Constants
# ==============================================================================

DEFAULT_FILTER_TYPE = "gaussian"
DEFAULT_BLUR_RADIUS = 15
DEFAULT_PADDING = 4
DEFAULT_PIXELATE_BLOCK = 12

SENSITIVE_ROLES = {
    "ROLE_PASSWORD_TEXT",
    "PASSWORD_TEXT",
    "PASSWORD-TEXT",
    "PASSWORD",
    "ROLE_SECRET",
    "SECRET",
}

SENSITIVE_STATES = {
    "STATE_PROTECTED",
    "PROTECTED",
    "SENSITIVE",
}

SENSITIVE_ATTR_KEYS = {
    "input-type",
    "is-password",
    "echo-mode",
    "security",
    "secret",
    "accessible-type",
    "tag",
}

SENSITIVE_ATTR_VALUES = {
    "password",
    "sensitive",
    "secret",
    "private",
    "credential",
    "auth-token",
    "token",
    "hidden",
}

DEFAULT_MOCK_WIDGETS = [
    {
        "id": "login.dialog.password_field",
        "name": "Password",
        "role": "ROLE_PASSWORD_TEXT",
        "states": ["STATE_ENABLED", "STATE_VISIBLE", "STATE_FOCUSABLE", "STATE_PROTECTED"],
        "attributes": {"input-type": "password", "echo-mode": "password"},
        "bounds": {"x": 120, "y": 240, "width": 260, "height": 36},
    },
    {
        "id": "settings.dialog.api_key",
        "name": "API Key",
        "role": "ROLE_TEXT",
        "states": ["STATE_ENABLED", "STATE_VISIBLE", "STATE_PROTECTED"],
        "attributes": {"security": "sensitive", "tag": "secret"},
        "bounds": {"x": 520, "y": 180, "width": 320, "height": 34},
    },
    {
        "id": "auth.dialog.mfa_token",
        "name": "MFA Security Token",
        "role": "ROLE_ENTRY",
        "states": ["STATE_ENABLED", "STATE_VISIBLE"],
        "attributes": {"accessible-type": "password", "secret": "true"},
        "bounds": {"x": 520, "y": 300, "width": 180, "height": 34},
    },
    # Non-sensitive widgets included in mock tree for negative control verification
    {
        "id": "login.dialog.username_field",
        "name": "Username",
        "role": "ROLE_TEXT",
        "states": ["STATE_ENABLED", "STATE_VISIBLE", "STATE_FOCUSABLE"],
        "attributes": {"input-type": "text"},
        "bounds": {"x": 120, "y": 160, "width": 260, "height": 36},
    },
    {
        "id": "login.dialog.submit_button",
        "name": "Sign In",
        "role": "ROLE_PUSH_BUTTON",
        "states": ["STATE_ENABLED", "STATE_VISIBLE"],
        "attributes": {"action": "submit"},
        "bounds": {"x": 120, "y": 310, "width": 100, "height": 40},
    },
    {
        "id": "settings.dialog.header_label",
        "name": "Account Security Settings",
        "role": "ROLE_LABEL",
        "states": ["STATE_VISIBLE"],
        "attributes": {"heading": "h2"},
        "bounds": {"x": 520, "y": 100, "width": 400, "height": 50},
    },
]


# ==============================================================================
# Data Models
# ==============================================================================

@dataclass
class BoundingBox:
    x: int
    y: int
    width: int
    height: int

    def to_dict(self) -> Dict[str, int]:
        return {
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
        }

    def pad(self, padding: int, max_w: int, max_h: int) -> Tuple[int, int, int, int]:
        """Returns clamped (x1, y1, x2, y2) bounds with padding applied."""
        x1 = max(0, self.x - padding)
        y1 = max(0, self.y - padding)
        x2 = min(max_w, self.x + self.width + padding)
        y2 = min(max_h, self.y + self.height + padding)
        return x1, y1, x2, y2

    def contains_point(self, px: int, py: int) -> bool:
        return self.x <= px < (self.x + self.width) and self.y <= py < (self.y + self.height)

    def intersects(self, other: "BoundingBox") -> bool:
        return not (
            self.x + self.width <= other.x
            or other.x + other.width <= self.x
            or self.y + self.height <= other.y
            or other.y + other.height <= self.y
        )


@dataclass
class SensitiveWidget:
    widget_id: str
    name: str
    role: str
    states: List[str]
    attributes: Dict[str, str]
    bounds: BoundingBox
    confidence: float = 1.0
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.widget_id,
            "name": self.name,
            "role": self.role,
            "states": self.states,
            "attributes": self.attributes,
            "bounds": self.bounds.to_dict(),
            "reason": self.reason,
            "confidence": self.confidence,
        }


@dataclass
class RedactionConfig:
    filter_type: str = DEFAULT_FILTER_TYPE
    radius: int = DEFAULT_BLUR_RADIUS
    padding: int = DEFAULT_PADDING
    pixelate_block_size: int = DEFAULT_PIXELATE_BLOCK
    sigma: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "filter_type": self.filter_type,
            "radius": self.radius,
            "padding": self.padding,
            "pixelate_block_size": self.pixelate_block_size,
            "sigma": self.sigma,
        }


# ==============================================================================
# Sensitive Widget Classification Logic
# ==============================================================================

def is_sensitive_widget(
    role: str,
    states: Sequence[str],
    attributes: Dict[str, str],
    name: str = "",
) -> Tuple[bool, str]:
    """Evaluates whether a widget is sensitive based on ATSPI role, state, and attributes.

    Returns (is_sensitive: bool, reason: str).
    """
    role_clean = role.strip()
    role_upper = role_clean.upper()

    # 1. Role matches ROLE_PASSWORD_TEXT or password keywords
    if role_upper in SENSITIVE_ROLES or "PASSWORD" in role_upper:
        return True, "role:password_text"

    # 2. State contains STATE_PROTECTED
    norm_states = {s.strip().upper() for s in states}
    for protected_state in SENSITIVE_STATES:
        if protected_state in norm_states:
            return True, f"state:{protected_state.lower()}"

    # 3. Attributes check (security, input-type, echo-mode, etc.)
    for k, v in attributes.items():
        k_lower = k.lower().strip()
        v_lower = str(v).lower().strip()
        if k_lower in SENSITIVE_ATTR_KEYS:
            if v_lower in SENSITIVE_ATTR_VALUES or "password" in v_lower or "secret" in v_lower:
                return True, f"attribute:{k_lower}={v_lower}"
            if k_lower in ("is-password", "secret") and v_lower in ("true", "1", "yes"):
                return True, f"attribute:{k_lower}={v_lower}"
            if k_lower == "echo-mode" and v_lower in ("password", "no-echo", "none"):
                return True, f"attribute:{k_lower}={v_lower}"
        if "password" in k_lower or "password" in v_lower:
            return True, f"attribute_keyword:{k_lower}"

    # 4. Name / label heuristic when role is text input or entry
    name_lower = name.lower().strip()
    if role_upper in ("ROLE_TEXT", "ROLE_ENTRY", "ROLE_EDITABLE_TEXT", "TEXT", "ENTRY"):
        if name_lower in ("password", "passcode", "pin", "api key", "secret token", "private key"):
            return True, f"name_heuristic:{name_lower}"

    return False, ""


# ==============================================================================
# ATSPI Tree Query Engine
# ==============================================================================

def query_atspi(
    mock: bool = False,
    mock_data: Optional[List[Dict[str, Any]]] = None,
    mock_file: Optional[str] = None,
) -> List[SensitiveWidget]:
    """Queries the ATSPI accessibility tree or mock dataset for sensitive widgets."""
    if mock:
        return _query_mock_tree(mock_data, mock_file)
    return _query_live_atspi()


def _query_mock_tree(
    mock_data: Optional[List[Dict[str, Any]]] = None,
    mock_file: Optional[str] = None,
) -> List[SensitiveWidget]:
    """Extracts sensitive widgets from mock definitions."""
    raw_widgets: List[Dict[str, Any]] = []

    if mock_file and os.path.exists(mock_file):
        try:
            with open(mock_file, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, list):
                    raw_widgets = loaded
                elif isinstance(loaded, dict) and "widgets" in loaded:
                    raw_widgets = loaded["widgets"]
        except Exception as e:
            log.warning(f"Failed to load mock tree from {mock_file}: {e}")

    if not raw_widgets:
        raw_widgets = mock_data if mock_data is not None else DEFAULT_MOCK_WIDGETS

    sensitive_found: List[SensitiveWidget] = []
    for item in raw_widgets:
        role = item.get("role", "")
        states = item.get("states", [])
        attrs = item.get("attributes", {})
        name = item.get("name", "")

        is_sens, reason = is_sensitive_widget(role, states, attrs, name)
        if is_sens:
            b = item.get("bounds", {})
            bbox = BoundingBox(
                x=int(b.get("x", 0)),
                y=int(b.get("y", 0)),
                width=int(b.get("width", 0)),
                height=int(b.get("height", 0)),
            )
            widget_id = item.get("id", f"widget_{bbox.x}_{bbox.y}")
            sensitive_found.append(
                SensitiveWidget(
                    widget_id=widget_id,
                    name=name,
                    role=role,
                    states=list(states),
                    attributes=dict(attrs),
                    bounds=bbox,
                    reason=reason,
                    confidence=1.0,
                )
            )

    return sensitive_found


def _query_live_atspi() -> List[SensitiveWidget]:
    """Queries live AT-SPI via gi.repository.Atspi if present."""
    try:
        import gi

        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi

        Atspi.init()
        desktop = Atspi.get_desktop(0)
    except Exception as e:
        log.debug(f"AT-SPI not available on live session: {e}")
        return []

    sensitive_found: List[SensitiveWidget] = []

    def walk(node: Any, depth: int = 0) -> None:
        if node is None or depth > 30:
            return
        try:
            role_name = node.get_role_name() or ""
            name = node.get_name() or ""

            states_list: List[str] = []
            try:
                ss = node.get_state_set()
                if ss.contains(Atspi.StateType.PROTECTED):
                    states_list.append("STATE_PROTECTED")
                if ss.contains(Atspi.StateType.VISIBLE):
                    states_list.append("STATE_VISIBLE")
                if ss.contains(Atspi.StateType.ENABLED):
                    states_list.append("STATE_ENABLED")
            except Exception:
                pass

            attrs: Dict[str, str] = {}
            try:
                raw_attrs = node.get_attributes()
                if isinstance(raw_attrs, dict):
                    attrs = raw_attrs
                elif isinstance(raw_attrs, list):
                    for a in raw_attrs:
                        if ":" in a:
                            k, v = a.split(":", 1)
                            attrs[k.strip()] = v.strip()
            except Exception:
                pass

            is_sens, reason = is_sensitive_widget(role_name, states_list, attrs, name)
            if is_sens:
                comp = node.get_component_iface()
                ext = comp.get_extents(Atspi.CoordType.SCREEN) if comp else None
                if ext and ext.width > 0 and ext.height > 0:
                    bbox = BoundingBox(x=ext.x, y=ext.y, width=ext.width, height=ext.height)
                    sensitive_found.append(
                        SensitiveWidget(
                            widget_id=f"{name or 'widget'}_{ext.x}_{ext.y}",
                            name=name,
                            role=role_name,
                            states=states_list,
                            attributes=attrs,
                            bounds=bbox,
                            reason=reason,
                            confidence=1.0,
                        )
                    )

            count = node.get_child_count()
            for i in range(count):
                walk(node.get_child_at_index(i), depth + 1)
        except Exception:
            pass

    try:
        walk(desktop)
    except Exception as e:
        log.debug(f"Error traversing AT-SPI tree: {e}")

    return sensitive_found


def check_atspi_status(mock: bool = False) -> Dict[str, Any]:
    """Reports status of ATSPI bus connection, detected widgets, and filter config."""
    if mock:
        widgets = _query_mock_tree()
        return {
            "atspi_connected": True,
            "backend": "mock",
            "bus_address": "mock://org.a11y.Bus/0",
            "sensitive_widgets_detected": len(widgets),
            "filter_config": RedactionConfig().to_dict(),
            "capabilities": {
                "pil_available": HAVE_PIL,
                "cv2_available": HAVE_CV2,
                "pure_python_engine": True,
                "supported_formats": ["png", "ppm", "bmp", "raw"],
            },
        }

    # Test live connection
    connected = False
    backend = "none"
    bus_address = os.environ.get("AT_SPI_BUS_ADDRESS", "")

    try:
        import gi

        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi

        if Atspi.init() == 0:
            connected = True
            backend = "gi_atspi"
    except Exception:
        pass

    if not connected and bus_address:
        connected = True
        backend = "dbus_env"

    widgets = _query_live_atspi() if connected else []

    return {
        "atspi_connected": connected,
        "backend": backend,
        "bus_address": bus_address or "none",
        "sensitive_widgets_detected": len(widgets),
        "filter_config": RedactionConfig().to_dict(),
        "capabilities": {
            "pil_available": HAVE_PIL,
            "cv2_available": HAVE_CV2,
            "pure_python_engine": True,
            "supported_formats": ["png", "ppm", "bmp", "raw"],
        },
    }


# ==============================================================================
# Frame Buffer & Image Processing Engine
# ==============================================================================

class FrameBuffer:
    """In-memory 24-bit RGB or 32-bit RGBA image frame buffer.

    Supports reading and writing PNG, BMP, PPM (P3/P6), and RAW formats.
    Provides pure-Python Gaussian blur, Box blur, and Pixelation filters.
    """

    def __init__(
        self,
        width: int,
        height: int,
        channels: int = 3,
        data: Optional[Union[bytearray, bytes]] = None,
    ):
        self.width = width
        self.height = height
        self.channels = channels
        expected_len = width * height * channels
        if data is None:
            self.data = bytearray(expected_len)
        else:
            if len(data) != expected_len:
                raise ValueError(
                    f"Data length {len(data)} does not match {width}x{height}x{channels} ({expected_len})"
                )
            self.data = bytearray(data)

    def clone(self) -> "FrameBuffer":
        return FrameBuffer(self.width, self.height, self.channels, self.data)

    # --------------------------------------------------------------------------
    # File I/O
    # --------------------------------------------------------------------------

    @classmethod
    def load(cls, path: Union[str, pathlib.Path], force_pure_python: bool = False) -> "FrameBuffer":
        p = pathlib.Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Input image file does not exist: {p}")

        if not force_pure_python and HAVE_PIL:
            try:
                img = PILImage.open(p)
                if img.mode not in ("RGB", "RGBA"):
                    img = img.convert("RGB")
                ch = 4 if img.mode == "RGBA" else 3
                return cls(img.width, img.height, ch, img.tobytes())
            except Exception as e:
                log.debug(f"PIL failed to load {p}, falling back to pure-Python: {e}")

        with open(p, "rb") as f:
            header = f.read(16)
            f.seek(0)
            raw = f.read()

        if len(raw) < 4:
            raise ValueError(f"Corrupted or empty image file: {p}")

        # PNG format
        if header.startswith(b"\x89PNG\r\n\x1a\n"):
            return cls._decode_png(raw)

        # BMP format
        if header.startswith(b"BM"):
            return cls._decode_bmp(raw)

        # PPM format
        if header.startswith(b"P6") or header.startswith(b"P3"):
            return cls._decode_ppm(raw)

        raise ValueError(f"Unsupported or corrupted image format: {p}")

    def save(self, path: Union[str, pathlib.Path], force_pure_python: bool = False) -> None:
        p = pathlib.Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        ext = p.suffix.lower()

        if not force_pure_python and HAVE_PIL:
            try:
                mode = "RGBA" if self.channels == 4 else "RGB"
                img = PILImage.frombytes(mode, (self.width, self.height), bytes(self.data))
                img.save(p)
                return
            except Exception as e:
                log.debug(f"PIL save failed for {p}, falling back to pure-Python: {e}")

        if ext == ".png":
            self._encode_png(p)
        elif ext == ".bmp":
            self._encode_bmp(p)
        elif ext in (".ppm", ".pnm"):
            self._encode_ppm(p)
        elif ext in (".raw", ".bin"):
            with open(p, "wb") as f:
                f.write(self.data)
        else:
            # Default fallback to PNG encoding
            self._encode_png(p)

    # --------------------------------------------------------------------------
    # Format Decoders / Encoders
    # --------------------------------------------------------------------------

    @classmethod
    def _decode_png(cls, data: bytes) -> "FrameBuffer":
        if len(data) < 8 or data[:8] != b"\x89PNG\r\n\x1a\n":
            raise ValueError("Invalid PNG magic bytes")

        idx = 8
        idat_chunks: List[bytes] = []
        width = height = depth = color_type = 0

        while idx < len(data):
            if idx + 8 > len(data):
                raise ValueError("Corrupted PNG chunk header")
            length = struct.unpack(">I", data[idx : idx + 4])[0]
            tag = data[idx + 4 : idx + 8]
            chunk_data = data[idx + 8 : idx + 8 + length]
            idx += 12 + length

            if tag == b"IHDR":
                width, height, depth, color_type = struct.unpack(">IIBB", chunk_data[:10])
                if depth != 8:
                    raise ValueError(f"Unsupported PNG bit depth: {depth}")
                if color_type not in (2, 6):
                    raise ValueError(f"Unsupported PNG color type: {color_type} (expected RGB or RGBA)")
            elif tag == b"IDAT":
                idat_chunks.append(chunk_data)
            elif tag == b"IEND":
                break

        if width <= 0 or height <= 0:
            raise ValueError("Invalid PNG dimensions")

        bpp = 3 if color_type == 2 else 4
        stride = width * bpp
        decompressed = zlib.decompress(b"".join(idat_chunks))

        def paeth(a: int, b: int, c: int) -> int:
            p = a + b - c
            pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
            if pa <= pb and pa <= pc:
                return a
            if pb <= pc:
                return b
            return c

        out = bytearray(height * stride)
        src_idx = 0
        for y in range(height):
            filter_type = decompressed[src_idx]
            src_idx += 1
            row = bytearray(decompressed[src_idx : src_idx + stride])
            src_idx += stride
            prev_row = out[(y - 1) * stride : y * stride] if y > 0 else bytearray(stride)

            for x in range(stride):
                a = row[x - bpp] if x >= bpp else 0
                b = prev_row[x]
                c = prev_row[x - bpp] if x >= bpp else 0
                if filter_type == 0:
                    pass
                elif filter_type == 1:
                    row[x] = (row[x] + a) & 0xFF
                elif filter_type == 2:
                    row[x] = (row[x] + b) & 0xFF
                elif filter_type == 3:
                    row[x] = (row[x] + ((a + b) // 2)) & 0xFF
                elif filter_type == 4:
                    row[x] = (row[x] + paeth(a, b, c)) & 0xFF
                else:
                    raise ValueError(f"Unknown PNG filter type {filter_type}")
            out[y * stride : (y + 1) * stride] = row

        return cls(width, height, bpp, out)

    def _encode_png(self, path: pathlib.Path) -> None:
        def make_chunk(tag: bytes, content: bytes) -> bytes:
            return (
                struct.pack(">I", len(content))
                + tag
                + content
                + struct.pack(">I", zlib.crc32(tag + content) & 0xFFFFFFFF)
            )

        hdr = b"\x89PNG\r\n\x1a\n"
        color_type = 2 if self.channels == 3 else 6
        ihdr_data = struct.pack(">IIBBBBB", self.width, self.height, 8, color_type, 0, 0, 0)
        ihdr = make_chunk(b"IHDR", ihdr_data)

        stride = self.width * self.channels
        scanlines = bytearray()
        for y in range(self.height):
            scanlines.append(0)  # filter type 0: None
            scanlines.extend(self.data[y * stride : (y + 1) * stride])

        idat = make_chunk(b"IDAT", zlib.compress(bytes(scanlines), level=6))
        iend = make_chunk(b"IEND", b"")

        with open(path, "wb") as f:
            f.write(hdr + ihdr + idat + iend)

    @classmethod
    def _decode_bmp(cls, data: bytes) -> "FrameBuffer":
        if len(data) < 54 or data[:2] != b"BM":
            raise ValueError("Invalid BMP header")

        offset = struct.unpack("<I", data[10:14])[0]
        w, h, planes, bpp, comp = struct.unpack("<iiHHI", data[18:34])

        if comp != 0:
            raise ValueError(f"Unsupported compressed BMP (comp={comp})")
        if bpp not in (24, 32):
            raise ValueError(f"Unsupported BMP bit depth: {bpp}")

        top_down = False
        if h < 0:
            h = -h
            top_down = True

        ch = 3 if bpp == 24 else 4
        row_pad = (4 - (w * (bpp // 8)) % 4) % 4
        row_len = w * (bpp // 8) + row_pad
        out = bytearray(w * h * ch)

        for y in range(h):
            src_y = y if top_down else (h - 1 - y)
            row_start = offset + src_y * row_len
            row = data[row_start : row_start + w * (bpp // 8)]
            for x in range(w):
                src_px = x * (bpp // 8)
                dst_px = (y * w + x) * ch
                # BMP stores BGR(A), convert to RGB(A)
                out[dst_px] = row[src_px + 2]
                out[dst_px + 1] = row[src_px + 1]
                out[dst_px + 2] = row[src_px]
                if ch == 4:
                    out[dst_px + 3] = row[src_px + 3]

        return cls(w, h, ch, out)

    def _encode_bmp(self, path: pathlib.Path) -> None:
        ch = self.channels
        bpp = ch * 8
        row_pad = (4 - (self.width * ch) % 4) % 4
        image_size = (self.width * ch + row_pad) * self.height
        file_size = 54 + image_size
        file_header = struct.pack("<2sIHHI", b"BM", file_size, 0, 0, 54)
        info_header = struct.pack(
            "<IIIHHIIIIII", 40, self.width, self.height, 1, bpp, 0, image_size, 2835, 2835, 0, 0
        )

        rows: List[bytes] = []
        pad_bytes = b"\x00" * row_pad
        for y in range(self.height - 1, -1, -1):
            row = bytearray(self.width * ch)
            src_row = self.data[y * self.width * ch : (y + 1) * self.width * ch]
            for x in range(self.width):
                # RGB(A) to BGR(A)
                row[x * ch] = src_row[x * ch + 2]
                row[x * ch + 1] = src_row[x * ch + 1]
                row[x * ch + 2] = src_row[x * ch]
                if ch == 4:
                    row[x * ch + 3] = src_row[x * ch + 3]
            rows.append(bytes(row) + pad_bytes)

        with open(path, "wb") as f:
            f.write(file_header + info_header + b"".join(rows))

    @classmethod
    def _decode_ppm(cls, data: bytes) -> "FrameBuffer":
        if not (data.startswith(b"P6") or data.startswith(b"P3")):
            raise ValueError("Not a valid PPM image")

        is_p6 = data.startswith(b"P6")
        idx = 2
        tokens: List[str] = []

        while len(tokens) < 3 and idx < len(data):
            while idx < len(data) and data[idx : idx + 1] in (b" ", b"\t", b"\r", b"\n"):
                idx += 1
            if idx < len(data) and data[idx : idx + 1] == b"#":
                while idx < len(data) and data[idx : idx + 1] not in (b"\r", b"\n"):
                    idx += 1
                continue
            start = idx
            while idx < len(data) and data[idx : idx + 1] not in (b" ", b"\t", b"\r", b"\n", b"#"):
                idx += 1
            if start < idx:
                tokens.append(data[start:idx].decode("ascii", errors="replace"))

        if len(tokens) < 3:
            raise ValueError("Invalid PPM header")

        w, h = int(tokens[0]), int(tokens[1])
        while idx < len(data) and data[idx : idx + 1] in (b" ", b"\t", b"\r", b"\n"):
            idx += 1

        if is_p6:
            pixel_bytes = data[idx : idx + w * h * 3]
            if len(pixel_bytes) < w * h * 3:
                raise ValueError("Truncated P6 PPM pixel data")
            return cls(w, h, 3, pixel_bytes)
        else:
            # P3 ASCII PPM
            num_tokens = data[idx:].decode("ascii", errors="replace").split()
            nums = [int(n) for n in num_tokens[: w * h * 3]]
            return cls(w, h, 3, bytes(nums))

    def _encode_ppm(self, path: pathlib.Path) -> None:
        header = f"P6\n{self.width} {self.height}\n255\n".encode("ascii")
        if self.channels == 3:
            pixel_data = bytes(self.data)
        else:
            # Strip alpha for standard P6 PPM
            rgb = bytearray(self.width * self.height * 3)
            for i in range(self.width * self.height):
                rgb[i * 3] = self.data[i * 4]
                rgb[i * 3 + 1] = self.data[i * 4 + 1]
                rgb[i * 3 + 2] = self.data[i * 4 + 2]
            pixel_data = bytes(rgb)

        with open(path, "wb") as f:
            f.write(header + pixel_data)

    # --------------------------------------------------------------------------
    # Redaction Filter Algorithms
    # --------------------------------------------------------------------------

    def apply_box_blur(self, x1: int, y1: int, x2: int, y2: int, radius: int) -> None:
        """Applies a sliding-window Box Blur to [x1, x2) x [y1, y2)."""
        if radius <= 0 or x2 <= x1 or y2 <= y1:
            return
        r = radius
        w, h, ch = self.width, self.height, self.channels
        inv = 1.0 / (2 * r + 1)

        x1 = max(0, min(x1, w))
        x2 = max(0, min(x2, w))
        y1 = max(0, min(y1, h))
        y2 = max(0, min(y2, h))
        if x2 <= x1 or y2 <= y1:
            return

        y_start = max(0, y1 - r)
        y_end = min(h, y2 + r)
        temp = bytearray(len(self.data))

        # Horizontal 1D pass
        for y in range(y_start, y_end):
            row_base = y * w * ch
            for c in range(ch):
                acc = 0
                for k in range(-r, r + 1):
                    px = max(0, min(x1 + k, w - 1))
                    acc += self.data[row_base + px * ch + c]
                temp[row_base + x1 * ch + c] = int(acc * inv)

                for x in range(x1 + 1, x2):
                    left_px = max(0, min(x - 1 - r, w - 1))
                    right_px = max(0, min(x + r, w - 1))
                    acc += self.data[row_base + right_px * ch + c] - self.data[row_base + left_px * ch + c]
                    temp[row_base + x * ch + c] = int(acc * inv)

        # Vertical 1D pass
        for x in range(x1, x2):
            for c in range(ch):
                acc = 0
                for k in range(-r, r + 1):
                    py = max(0, min(y1 + k, h - 1))
                    acc += temp[py * w * ch + x * ch + c]
                self.data[y1 * w * ch + x * ch + c] = int(acc * inv)

                for y in range(y1 + 1, y2):
                    top_py = max(0, min(y - 1 - r, h - 1))
                    bot_py = max(0, min(y + r, h - 1))
                    acc += temp[bot_py * w * ch + x * ch + c] - temp[top_py * w * ch + x * ch + c]
                    self.data[y * w * ch + x * ch + c] = int(acc * inv)

    def apply_gaussian_blur(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        radius: int,
        sigma: Optional[float] = None,
    ) -> None:
        """Applies Gaussian blur via multi-pass sliding box approximation or discrete kernel."""
        if radius <= 0 or x2 <= x1 or y2 <= y1:
            return

        # 3 passes of box blur approximate a Gaussian distribution with high fidelity (CLT)
        # Using adjusted radii r_box ~ sqrt(12 * sigma^2 / 3 + 1)
        if sigma is None or sigma <= 0:
            sigma = max(1.0, radius / 2.5)

        # Execute 3 successive box blurs with radius r_pass
        r_pass = max(1, int(round(radius * 0.57)))
        for _ in range(3):
            self.apply_box_blur(x1, y1, x2, y2, r_pass)

    def apply_pixelate(self, x1: int, y1: int, x2: int, y2: int, block_size: int) -> None:
        """Applies mosaic pixelation to [x1, x2) x [y1, y2) with given block size."""
        if block_size <= 1 or x2 <= x1 or y2 <= y1:
            return
        bs = block_size
        w, h, ch = self.width, self.height, self.channels
        x1 = max(0, min(x1, w))
        x2 = max(0, min(x2, w))
        y1 = max(0, min(y1, h))
        y2 = max(0, min(y2, h))

        for by in range(y1, y2, bs):
            by_end = min(by + bs, y2)
            for bx in range(x1, x2, bs):
                bx_end = min(bx + bs, x2)
                count = (by_end - by) * (bx_end - bx)
                if count == 0:
                    continue

                sums = [0] * ch
                for py in range(by, by_end):
                    row_base = py * w * ch
                    for px in range(bx, bx_end):
                        idx = row_base + px * ch
                        for c in range(ch):
                            sums[c] += self.data[idx + c]

                avg = [s // count for s in sums]

                for py in range(by, by_end):
                    row_base = py * w * ch
                    for px in range(bx, bx_end):
                        idx = row_base + px * ch
                        for c in range(ch):
                            self.data[idx + c] = avg[c]

    def redact_box(self, bbox: BoundingBox, config: RedactionConfig) -> None:
        """Redacts a single bounding box region using configured filter and padding."""
        x1, y1, x2, y2 = bbox.pad(config.padding, self.width, self.height)
        ftype = config.filter_type.lower()

        if ftype in ("gaussian", "gaussian_blur", "blur"):
            self.apply_gaussian_blur(x1, y1, x2, y2, config.radius, config.sigma)
        elif ftype in ("box", "box_blur"):
            self.apply_box_blur(x1, y1, x2, y2, config.radius)
        elif ftype in ("pixelate", "mosaic"):
            self.apply_pixelate(x1, y1, x2, y2, config.pixelate_block_size)
        else:
            # Fallback to Gaussian blur
            self.apply_gaussian_blur(x1, y1, x2, y2, config.radius, config.sigma)


# ==============================================================================
# High-Level Redaction Pipeline
# ==============================================================================

def redact_frame(
    input_path: Union[str, pathlib.Path],
    output_path: Union[str, pathlib.Path],
    widgets: Optional[List[SensitiveWidget]] = None,
    boxes: Optional[List[BoundingBox]] = None,
    config: Optional[RedactionConfig] = None,
    dry_run: bool = False,
    force_pure_python: bool = False,
    mock: bool = False,
) -> Dict[str, Any]:
    """Applies privacy redaction filters to sensitive widget regions in an image."""
    cfg = config or RedactionConfig()
    frame = FrameBuffer.load(input_path, force_pure_python=force_pure_python)

    # Resolve bounding boxes
    target_boxes: List[BoundingBox] = []

    if boxes is not None:
        target_boxes.extend(boxes)
    elif widgets is not None:
        target_boxes.extend([w.bounds for w in widgets])
    else:
        # Discover widgets via ATSPI or mock
        discovered = query_atspi(mock=mock)
        target_boxes.extend([w.bounds for w in discovered])

    redacted_info = []
    for b in target_boxes:
        x1, y1, x2, y2 = b.pad(cfg.padding, frame.width, frame.height)
        redacted_info.append({
            "bounds": b.to_dict(),
            "padded_bounds": {"x1": x1, "y1": y1, "x2": x2, "y2": y2},
            "filter": cfg.filter_type,
            "radius": cfg.radius,
        })
        if not dry_run:
            frame.redact_box(b, cfg)

    if not dry_run:
        frame.save(output_path, force_pure_python=force_pure_python)

    return {
        "status": "ok",
        "dry_run": dry_run,
        "input": str(input_path),
        "output": str(output_path) if not dry_run else None,
        "regions_redacted": len(target_boxes),
        "details": redacted_info,
        "dimensions": {"width": frame.width, "height": frame.height},
    }


# ==============================================================================
# CLI Entry Points
# ==============================================================================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mios_vision_redact",
        description=(
            "ATSPI accessibility tree sensitive widget coordinate detector "
            "and Wayland frame blur filter (task_483, task_2716)."
        ),
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose logging")
    parser.add_argument("--mock", action="store_true", help="Use mock ATSPI accessibility tree")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without writing files")

    subparsers = parser.add_subparsers(dest="subcommand", help="Available subcommands")

    # Subcommand: scan
    p_scan = subparsers.add_parser("scan", help="Scan accessible widgets and output bounding boxes as JSON")
    p_scan.add_argument("--mock", action="store_true", help="Use mock ATSPI tree")
    p_scan.add_argument("--mock-file", type=str, default=None, help="Custom mock widgets JSON file")
    p_scan.add_argument("--dry-run", action="store_true", help="Simulate scan")
    p_scan.add_argument("-v", "--verbose", action="store_true", help="Verbose output")

    # Subcommand: redact
    p_redact = subparsers.add_parser("redact", help="Apply redaction filter to image")
    p_redact.add_argument("--input", "-i", type=str, required=True, help="Input image file path")
    p_redact.add_argument("--output", "-o", type=str, required=True, help="Output image file path")
    p_redact.add_argument(
        "--filter",
        choices=["gaussian", "box", "pixelate"],
        default=DEFAULT_FILTER_TYPE,
        help=f"Redaction filter type (default: {DEFAULT_FILTER_TYPE})",
    )
    p_redact.add_argument(
        "--radius",
        type=int,
        default=DEFAULT_BLUR_RADIUS,
        help=f"Blur radius in pixels (default: {DEFAULT_BLUR_RADIUS})",
    )
    p_redact.add_argument(
        "--padding",
        type=int,
        default=DEFAULT_PADDING,
        help=f"Bounding box padding in pixels (default: {DEFAULT_PADDING})",
    )
    p_redact.add_argument(
        "--pixelate-block",
        type=int,
        default=DEFAULT_PIXELATE_BLOCK,
        help=f"Block size for pixelation (default: {DEFAULT_PIXELATE_BLOCK})",
    )
    p_redact.add_argument(
        "--boxes",
        type=str,
        default=None,
        help="JSON string or file containing bounding boxes to redact",
    )
    p_redact.add_argument("--mock", action="store_true", help="Use mock ATSPI tree if scanning")
    p_redact.add_argument("--mock-file", type=str, default=None, help="Custom mock widgets JSON file")
    p_redact.add_argument("--pure-python", action="store_true", help="Force pure-Python processing engine")
    p_redact.add_argument("--dry-run", action="store_true", help="Simulate without modifying or saving files")
    p_redact.add_argument("-v", "--verbose", action="store_true", help="Verbose output")

    # Subcommand: status
    p_status = subparsers.add_parser("status", help="Report ATSPI bus connection and filter configuration")
    p_status.add_argument("--mock", action="store_true", help="Report mock ATSPI status")
    p_status.add_argument("--json", action="store_true", help="Format status output as JSON")
    p_status.add_argument("-v", "--verbose", action="store_true", help="Verbose output")

    return parser


def cmd_scan(args: argparse.Namespace) -> int:
    is_mock = getattr(args, "mock", False)
    mock_file = getattr(args, "mock_file", None)
    widgets = query_atspi(mock=is_mock, mock_file=mock_file)

    payload = {
        "status": "ok",
        "backend": "mock" if is_mock else "live",
        "count": len(widgets),
        "widgets": [w.to_dict() for w in widgets],
    }
    print(json.dumps(payload, indent=2))
    return 0


def cmd_redact(args: argparse.Namespace) -> int:
    is_mock = getattr(args, "mock", False)
    is_dry_run = getattr(args, "dry_run", False)
    force_pure = getattr(args, "pure_python", False)

    cfg = RedactionConfig(
        filter_type=args.filter,
        radius=args.radius,
        padding=args.padding,
        pixelate_block_size=getattr(args, "pixelate_block", DEFAULT_PIXELATE_BLOCK),
    )

    explicit_boxes: Optional[List[BoundingBox]] = None
    if args.boxes:
        try:
            boxes_raw = args.boxes.strip()
            if os.path.exists(boxes_raw):
                with open(boxes_raw, "r", encoding="utf-8") as f:
                    parsed = json.load(f)
            else:
                parsed = json.loads(boxes_raw)

            if isinstance(parsed, dict) and "widgets" in parsed:
                parsed = [w["bounds"] for w in parsed["widgets"] if "bounds" in w]
            elif isinstance(parsed, dict) and "boxes" in parsed:
                parsed = parsed["boxes"]

            explicit_boxes = [
                BoundingBox(
                    x=int(b["x"]),
                    y=int(b["y"]),
                    width=int(b["width"]),
                    height=int(b["height"]),
                )
                for b in parsed
            ]
        except Exception as e:
            print(f"Error parsing --boxes: {e}", file=sys.stderr)
            return 1

    try:
        res = redact_frame(
            input_path=args.input,
            output_path=args.output,
            boxes=explicit_boxes,
            config=cfg,
            dry_run=is_dry_run,
            force_pure_python=force_pure,
            mock=is_mock,
        )
        if args.verbose or is_dry_run:
            print(json.dumps(res, indent=2))
        else:
            log.info(
                f"Successfully redacted {res['regions_redacted']} region(s) from {args.input} -> {args.output}"
            )
            print(f"OK: {res['regions_redacted']} sensitive region(s) redacted.")
        return 0
    except Exception as e:
        print(f"Redaction failed: {e}", file=sys.stderr)
        return 1


def cmd_status(args: argparse.Namespace) -> int:
    is_mock = getattr(args, "mock", False)
    as_json = getattr(args, "json", False)

    status_data = check_atspi_status(mock=is_mock)
    if as_json:
        print(json.dumps(status_data, indent=2))
        return 0

    print("[MiOS Vision Redaction Status]")
    print(f"  ATSPI Connected:    {status_data['atspi_connected']} (backend: {status_data['backend']})")
    print(f"  Bus Address:        {status_data['bus_address']}")
    print(f"  Sensitive Widgets:  {status_data['sensitive_widgets_detected']} detected")
    print(
        f"  Filter Config:      {status_data['filter_config']['filter_type']} "
        f"(radius={status_data['filter_config']['radius']}, padding={status_data['filter_config']['padding']})"
    )
    pil_str = "available" if status_data["capabilities"]["pil_available"] else "unavailable"
    cv2_str = "available" if status_data["capabilities"]["cv2_available"] else "unavailable"
    print(f"  Engine Backends:    Pure-Python (active), PIL ({pil_str}), OpenCV ({cv2_str})")
    print(f"  Supported Formats:  {', '.join(status_data['capabilities']['supported_formats'])}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    level = logging.DEBUG if getattr(args, "verbose", False) else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s [%(levelname)s] %(message)s")

    # Inherit top-level flags to subcommands if present
    if getattr(args, "mock", False) and hasattr(args, "subcommand"):
        setattr(args, "mock", True)
    if getattr(args, "dry_run", False) and hasattr(args, "subcommand"):
        setattr(args, "dry_run", True)

    if not args.subcommand:
        parser.print_help()
        return 0

    if args.subcommand == "scan":
        return cmd_scan(args)
    elif args.subcommand == "redact":
        return cmd_redact(args)
    elif args.subcommand == "status":
        return cmd_status(args)
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
