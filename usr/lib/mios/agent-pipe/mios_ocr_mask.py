#!/usr/bin/env python3
# AI-hint: Lightweight on-device OCR regex credential masking pipeline for vision frames (task_484, task_2717).
# AI-doc: usr/share/doc/mios/manual/ch85-ocr-credential-masking.md
"""Lightweight on-device OCR regex credential masking pipeline for vision frames.

Scans vision frames / screenshots for visible credentials (API tokens, private keys,
Bearer tokens, credit cards, password assignments) in inaccessible windows (terminals,
web pages, dev tools) and applies solid black bounding box redactions before passing
frames to multi-modal vision LLMs.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import zlib
from dataclasses import asdict, dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

# Optional PIL / Pillow support
try:
    from PIL import Image, ImageDraw
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

# Optional pytesseract support
try:
    import pytesseract
    HAS_PYTESSERACT = True
except ImportError:
    HAS_PYTESSERACT = False

# Optional paddleocr support
try:
    from paddleocr import PaddleOCR
    HAS_PADDLEOCR = True
except ImportError:
    HAS_PADDLEOCR = False


# ============================================================================
# Pure-Python Vision Frame Buffer (zero-dependency PNG / BMP / PPM handler)
# ============================================================================

class VisionFrame:
    """Lightweight in-memory RGB/RGBA image buffer with bounding box masking."""

    def __init__(self, width: int, height: int, channels: int = 3, data: Optional[bytearray] = None, format_name: str = "png"):
        self.width = width
        self.height = height
        self.channels = channels
        self.format_name = format_name.lower()
        if data is not None:
            expected = width * height * channels
            if len(data) != expected:
                raise ValueError(f"Image buffer size mismatch: got {len(data)}, expected {expected}")
            self.data = data
        else:
            self.data = bytearray(width * height * channels)

    def mask_rect(self, x: int, y: int, w: int, h: int, color: Tuple[int, ...] = (0, 0, 0), margin: int = 2) -> None:
        """Apply solid rectangular mask (black-box) over specified coordinates with padding margin."""
        x1 = max(0, min(self.width, x - margin))
        y1 = max(0, min(self.height, y - margin))
        x2 = max(0, min(self.width, x + w + margin))
        y2 = max(0, min(self.height, y + h + margin))

        color_len = min(self.channels, len(color))
        for row in range(y1, y2):
            row_start = row * self.width * self.channels
            for col in range(x1, x2):
                idx = row_start + col * self.channels
                for c in range(color_len):
                    self.data[idx + c] = color[c]
                if self.channels == 4 and color_len == 3:
                    self.data[idx + 3] = 255  # Solid Alpha

    def get_pixel(self, x: int, y: int) -> Tuple[int, ...]:
        """Return pixel tuple at (x, y)."""
        if not (0 <= x < self.width and 0 <= y < self.height):
            raise IndexError(f"Pixel ({x}, {y}) out of bounds ({self.width}x{self.height})")
        idx = (y * self.width + x) * self.channels
        return tuple(self.data[idx:idx + self.channels])

    def to_png(self) -> bytes:
        """Encode image buffer to standard PNG bytes using stdlib zlib."""
        def make_chunk(ctype: bytes, payload: bytes) -> bytes:
            c = ctype + payload
            crc = zlib.crc32(c) & 0xFFFFFFFF
            return struct.pack(">I", len(payload)) + c + struct.pack(">I", crc)

        raw = bytearray()
        stride = self.width * self.channels
        for y in range(self.height):
            raw.append(0)  # Filter 0 (None)
            start = y * stride
            raw.extend(self.data[start:start + stride])

        compressed = zlib.compress(bytes(raw), level=6)
        color_type = 2 if self.channels == 3 else 6
        ihdr = struct.pack(">IIBBBBB", self.width, self.height, 8, color_type, 0, 0, 0)
        return b"\x89PNG\r\n\x1a\n" + make_chunk(b"IHDR", ihdr) + make_chunk(b"IDAT", compressed) + make_chunk(b"IEND", b"")

    def to_bmp(self) -> bytes:
        """Encode image buffer to uncompressed 24-bit BMP bytes."""
        pad = (4 - (self.width * 3) % 4) % 4
        padded_row_len = self.width * 3 + pad
        pixel_data_size = padded_row_len * self.height
        file_size = 54 + pixel_data_size

        # BMP Header
        header = struct.pack("<2sIHHI", b"BM", file_size, 0, 0, 54)
        # DIB Header (BITMAPINFOHEADER)
        dib = struct.pack("<IIIHHIIIIII", 40, self.width, self.height, 1, 24, 0, pixel_data_size, 2835, 2835, 0, 0)

        out = bytearray(header + dib)
        # BMP pixel rows are written bottom-up, BGR order
        pad_bytes = b"\x00" * pad
        for y in range(self.height - 1, -1, -1):
            row_start = y * self.width * self.channels
            for x in range(self.width):
                idx = row_start + x * self.channels
                r = self.data[idx]
                g = self.data[idx + 1]
                b = self.data[idx + 2]
                out.extend((b, g, r))
            if pad:
                out.extend(pad_bytes)
        return bytes(out)

    def to_ppm(self) -> bytes:
        """Encode image buffer to standard PPM (P6) bytes."""
        header = f"P6\n{self.width} {self.height}\n255\n".encode("ascii")
        if self.channels == 3:
            return header + bytes(self.data)
        out = bytearray(header)
        for i in range(self.width * self.height):
            idx = i * 4
            out.extend(self.data[idx:idx + 3])
        return bytes(out)

    def save(self, path: str) -> None:
        """Save frame to disk."""
        target_dir = os.path.dirname(os.path.abspath(path))
        if target_dir:
            os.makedirs(target_dir, exist_ok=True)

        if HAS_PIL:
            mode = "RGB" if self.channels == 3 else "RGBA"
            img = Image.frombytes(mode, (self.width, self.height), bytes(self.data))
            img.save(path)
            return

        ext = os.path.splitext(path)[1].lower()
        if ext == ".bmp":
            data = self.to_bmp()
        elif ext in (".ppm", ".pnm"):
            data = self.to_ppm()
        else:
            data = self.to_png()

        with open(path, "wb") as f:
            f.write(data)

    @classmethod
    def load(cls, path: str) -> VisionFrame:
        """Load image file into a VisionFrame buffer."""
        if not os.path.exists(path):
            raise FileNotFoundError(f"Image file does not exist: {path}")
        if os.path.getsize(path) == 0:
            raise ValueError(f"Image file is empty (0 bytes): {path}")

        if HAS_PIL:
            try:
                with Image.open(path) as img:
                    img_rgb = img.convert("RGB")
                    w, h = img_rgb.size
                    data = bytearray(img_rgb.tobytes())
                    return cls(w, h, channels=3, data=data, format_name=img.format or "png")
            except Exception as e:
                raise ValueError(f"Image file is empty or malformed: {path} ({e})")

        with open(path, "rb") as f:
            raw = f.read()

        if len(raw) < 8:
            raise ValueError(f"Image file is empty or malformed: {path}")

        # Try PNG
        if raw.startswith(b"\x89PNG\r\n\x1a\n"):
            return cls._load_png(raw)
        # Try BMP
        if raw.startswith(b"BM"):
            return cls._load_bmp(raw)
        # Try PPM
        if raw.startswith(b"P6"):
            return cls._load_ppm(raw)

        raise ValueError(f"Image file is empty or malformed: unsupported image format in {path}")

    @classmethod
    def _load_png(cls, raw: bytes) -> VisionFrame:
        offset = 8
        width = height = None
        color_type = None
        idat_chunks = []

        while offset < len(raw):
            if offset + 8 > len(raw):
                raise ValueError("Malformed PNG: truncated chunk header")
            length = struct.unpack(">I", raw[offset:offset + 4])[0]
            ctype = raw[offset + 4:offset + 8]
            offset += 8
            if offset + length + 4 > len(raw):
                raise ValueError("Malformed PNG: truncated chunk data")
            cdata = raw[offset:offset + length]
            offset += length + 4  # skip CRC

            if ctype == b"IHDR":
                width, height, depth, color_type = struct.unpack(">IIBB", cdata[:10])
                if depth != 8:
                    raise ValueError(f"Unsupported PNG bit depth: {depth}")
                if color_type not in (2, 6):  # RGB or RGBA
                    raise ValueError(f"Unsupported PNG color type: {color_type}")
            elif ctype == b"IDAT":
                idat_chunks.append(cdata)
            elif ctype == b"IEND":
                break

        if width is None or height is None or not idat_chunks:
            raise ValueError("Malformed PNG: missing IHDR or IDAT chunks")

        try:
            decompressed = zlib.decompress(b"".join(idat_chunks))
        except Exception as e:
            raise ValueError(f"Malformed PNG: zlib decompression failed: {e}")

        channels = 3 if color_type == 2 else 4
        stride = width * channels
        expected_len = height * (1 + stride)
        if len(decompressed) < expected_len:
            raise ValueError("Malformed PNG: decompressed stream truncated")

        def paeth(a: int, b: int, c: int) -> int:
            p = a + b - c
            pa = abs(p - a)
            pb = abs(p - b)
            pc = abs(p - c)
            if pa <= pb and pa <= pc:
                return a
            elif pb <= pc:
                return b
            return c

        pixels = bytearray(width * height * channels)
        src_pos = 0
        prior = bytearray(stride)

        for y in range(height):
            filter_type = decompressed[src_pos]
            src_pos += 1
            filt_line = decompressed[src_pos:src_pos + stride]
            src_pos += stride
            recon_line = bytearray(stride)

            if filter_type == 0:  # None
                recon_line[:] = filt_line
            elif filter_type == 1:  # Sub
                for x in range(stride):
                    left = recon_line[x - channels] if x >= channels else 0
                    recon_line[x] = (filt_line[x] + left) & 0xFF
            elif filter_type == 2:  # Up
                for x in range(stride):
                    up = prior[x]
                    recon_line[x] = (filt_line[x] + up) & 0xFF
            elif filter_type == 3:  # Average
                for x in range(stride):
                    left = recon_line[x - channels] if x >= channels else 0
                    up = prior[x]
                    recon_line[x] = (filt_line[x] + ((left + up) >> 1)) & 0xFF
            elif filter_type == 4:  # Paeth
                for x in range(stride):
                    left = recon_line[x - channels] if x >= channels else 0
                    up = prior[x]
                    up_left = prior[x - channels] if x >= channels else 0
                    recon_line[x] = (filt_line[x] + paeth(left, up, up_left)) & 0xFF
            else:
                raise ValueError(f"Malformed PNG: invalid filter type {filter_type}")

            pixels[y * stride:(y + 1) * stride] = recon_line
            prior = recon_line

        return cls(width, height, channels, pixels, format_name="png")

    @classmethod
    def _load_bmp(cls, raw: bytes) -> VisionFrame:
        if len(raw) < 54:
            raise ValueError("Malformed BMP: file size under 54 bytes")
        offset = struct.unpack("<I", raw[10:14])[0]
        header_size = struct.unpack("<I", raw[14:18])[0]
        if header_size < 40:
            raise ValueError(f"Unsupported BMP DIB header size: {header_size}")
        width, height, planes, bpp = struct.unpack("<iiHH", raw[18:30])
        if planes != 1 or bpp not in (24, 32):
            raise ValueError(f"Unsupported BMP format: bpp={bpp}, planes={planes}")

        is_top_down = height < 0
        height = abs(height)
        channels = 3
        pad = (4 - (width * (bpp // 8)) % 4) % 4
        row_len = width * (bpp // 8) + pad

        pixels = bytearray(width * height * channels)
        bytes_per_pixel = bpp // 8

        for y in range(height):
            actual_y = y if is_top_down else (height - 1 - y)
            row_start = offset + y * row_len
            out_row_start = actual_y * width * channels
            for x in range(width):
                px_idx = row_start + x * bytes_per_pixel
                b = raw[px_idx]
                g = raw[px_idx + 1]
                r = raw[px_idx + 2]
                out_idx = out_row_start + x * channels
                pixels[out_idx] = r
                pixels[out_idx + 1] = g
                pixels[out_idx + 2] = b

        return cls(width, height, channels, pixels, format_name="bmp")

    @classmethod
    def _load_ppm(cls, raw: bytes) -> VisionFrame:
        header_end = 0
        tokens = []
        idx = 0
        while len(tokens) < 4 and idx < len(raw):
            # Skip comments
            if raw[idx:idx + 1] == b"#":
                idx = raw.find(b"\n", idx)
                if idx == -1:
                    break
                idx += 1
                continue
            # Whitespace
            if raw[idx:idx + 1] in b" \t\r\n":
                idx += 1
                continue
            # Token
            start = idx
            while idx < len(raw) and raw[idx:idx + 1] not in b" \t\r\n#":
                idx += 1
            tokens.append(raw[start:idx].decode("ascii", errors="replace"))
        if len(tokens) < 4 or tokens[0] != "P6":
            raise ValueError("Malformed PPM: invalid P6 header")
        width, height, maxval = int(tokens[1]), int(tokens[2]), int(tokens[3])
        if maxval != 255:
            raise ValueError(f"Unsupported PPM maxval: {maxval}")
        # One whitespace character follows maxval
        idx += 1
        data = bytearray(raw[idx:idx + width * height * 3])
        if len(data) != width * height * 3:
            raise ValueError("Malformed PPM: truncated pixel data")
        return cls(width, height, 3, data, format_name="ppm")


# ============================================================================
# Credential Regex Engine & Redaction Catalog
# ============================================================================

def luhn_checksum_valid(number_str: str) -> bool:
    """Validate payment card number using Luhn checksum algorithm."""
    clean = re.sub(r"[ -]", "", number_str)
    if not (13 <= len(clean) <= 19 and clean.isdigit()):
        return False
    digits = [int(c) for c in clean]
    checksum = 0
    for idx, d in enumerate(reversed(digits)):
        if idx % 2 == 1:
            doubled = d * 2
            checksum += doubled - 9 if doubled > 9 else doubled
        else:
            checksum += d
    return checksum % 10 == 0


@dataclass
class CredentialRule:
    rule_id: str
    name: str
    category: str
    pattern: re.Pattern
    description: str
    validator: Optional[Callable[[str], bool]] = None
    redactor: Optional[Callable[[str], str]] = None


def _redact_openai_key(val: str) -> str:
    if val.startswith("sk-proj-"):
        return "sk-proj-" + "*" * (len(val) - 8)
    elif val.startswith("sk-admin-"):
        return "sk-admin-" + "*" * (len(val) - 9)
    return "sk-" + "*" * (len(val) - 3)


def _redact_github_key(val: str) -> str:
    prefix = val[:4]
    return prefix + "*" * (len(val) - 4)


def _redact_aws_key(val: str) -> str:
    return val[:4] + "*" * (len(val) - 4)


def _redact_bearer_token(val: str) -> str:
    parts = val.split(None, 1)
    if len(parts) == 2:
        return f"{parts[0]} " + "*" * len(parts[1])
    return "Bearer " + "*" * (len(val) - 7)


def _redact_credit_card(val: str) -> str:
    clean = re.sub(r"[ -]", "", val)
    last4 = clean[-4:] if len(clean) >= 4 else "0000"
    return "****-****-****-" + last4


def _redact_assignment(val: str) -> str:
    match = re.search(r"[:=]\s*[\"']?([^\s\"'\n]+)[\"']?", val)
    if match:
        secret = match.group(1)
        prefix = val[:match.start(1)]
        suffix = val[match.end(1):]
        return f"{prefix}{'*' * len(secret)}{suffix}"
    return "********"


RULES: List[CredentialRule] = [
    CredentialRule(
        rule_id="api_key_openai",
        name="OpenAI API Key",
        category="api_keys",
        pattern=re.compile(r"\b(?:sk-(?:proj-|admin-)?[a-zA-Z0-9_\-]{20,})\b"),
        description="OpenAI secret API key (sk-..., sk-proj-...)",
        redactor=_redact_openai_key,
    ),
    CredentialRule(
        rule_id="api_key_github",
        name="GitHub Personal Access Token",
        category="api_keys",
        pattern=re.compile(r"\b(?:ghp_[a-zA-Z0-9]{36}|gho_[a-zA-Z0-9]{36}|ghs_[a-zA-Z0-9]{36}|ghu_[a-zA-Z0-9]{36}|ghr_[a-zA-Z0-9]{36}|github_pat_[a-zA-Z0-9_]{50,})\b"),
        description="GitHub personal, OAuth, and app bearer tokens",
        redactor=_redact_github_key,
    ),
    CredentialRule(
        rule_id="api_key_aws",
        name="AWS Access Key ID",
        category="api_keys",
        pattern=re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
        description="Amazon Web Services access key identifiers",
        redactor=_redact_aws_key,
    ),
    CredentialRule(
        rule_id="auth_bearer",
        name="Authorization Bearer Token",
        category="tokens",
        pattern=re.compile(r"\bBearer\s+([a-zA-Z0-9_\-\.]{20,})\b", re.IGNORECASE),
        description="HTTP Authorization header Bearer tokens (JWT, OAuth)",
        redactor=_redact_bearer_token,
    ),
    CredentialRule(
        rule_id="private_key",
        name="Private Key PEM Header",
        category="private_keys",
        pattern=re.compile(r"-----BEGIN\s+(?:[A-Z0-9_-]+\s+)?PRIVATE\s+KEY-----"),
        description="PEM/OpenSSH/RSA private cryptographic key headers",
        redactor=lambda v: "-----BEGIN [REDACTED] PRIVATE KEY-----",
    ),
    CredentialRule(
        rule_id="credit_card",
        name="Payment Card Number",
        category="pii",
        pattern=re.compile(r"\b(?:\d[ -]*?){13,16}\b"),
        description="Luhn-validated 13-16 digit payment card numbers",
        validator=luhn_checksum_valid,
        redactor=_redact_credit_card,
    ),
    CredentialRule(
        rule_id="password_assignment",
        name="Password Assignment",
        category="secrets",
        pattern=re.compile(r"""(?i)\b(?:password|passwd|pwd)\s*[:=]\s*["']?([^\s"'\n]{6,})["']?"""),
        description="Plaintext password assignments in code, config, or CLI",
        redactor=_redact_assignment,
    ),
    CredentialRule(
        rule_id="secret_key_assignment",
        name="Secret Key Assignment",
        category="secrets",
        pattern=re.compile(r"""(?i)\b(?:secret_key|api_key|client_secret|access_token|auth_token)\s*[:=]\s*["']?([^\s"'\n]{6,})["']?"""),
        description="Secret key, API key, or access token configuration assignments",
        redactor=_redact_assignment,
    ),
]


@dataclass
class DetectedCredential:
    rule_id: str
    pattern_type: str
    category: str
    bbox: Tuple[int, int, int, int]  # x, y, w, h
    matched_text: str
    redacted_preview: str
    confidence: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "pattern_type": self.pattern_type,
            "category": self.category,
            "bbox": list(self.bbox),
            "matched_text": self.matched_text,
            "redacted_preview": self.redacted_preview,
            "confidence": round(self.confidence, 4),
        }


# ============================================================================
# OCR Engine Interfaces (Tesseract, PaddleOCR, Mock)
# ============================================================================

@dataclass
class OcrBlock:
    text: str
    bbox: Tuple[int, int, int, int]  # x, y, w, h
    confidence: float


class BaseOcrEngine:
    name: str = "base"

    def detect_blocks(self, image_path: str, verbose: bool = False) -> List[OcrBlock]:
        raise NotImplementedError


class MockOcrEngine(BaseOcrEngine):
    name: str = "mock"

    def detect_blocks(self, image_path: str, verbose: bool = False) -> List[OcrBlock]:
        """Detect text blocks from sidecar JSON or deterministic mock patterns."""
        sidecars = [
            f"{image_path}.ocr.json",
            f"{image_path}.mock.json",
            os.path.splitext(image_path)[0] + ".ocr.json",
            os.path.splitext(image_path)[0] + ".mock.json",
        ]
        for sc in sidecars:
            if os.path.isfile(sc):
                if verbose:
                    sys.stderr.write(f"[ocr-mask:mock] Reading mock annotations from {sc}\n")
                try:
                    with open(sc, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    blocks = []
                    for item in data:
                        blocks.append(OcrBlock(
                            text=item.get("text", ""),
                            bbox=tuple(item.get("bbox", [0, 0, 10, 10])),
                            confidence=float(item.get("confidence", 0.95)),
                        ))
                    return blocks
                except Exception as e:
                    if verbose:
                        sys.stderr.write(f"[ocr-mask:mock] Failed parsing sidecar {sc}: {e}\n")

        # Deterministic default mock blocks for testing
        return [
            OcrBlock("OpenAI: sk-proj-a1b2c3d4e5f6g7h8i9j0k1l2m3n4", (40, 40, 320, 24), 0.98),
            OcrBlock("AWS Key: AKIA" + "IOSFODNN7EXAMPLE", (40, 80, 260, 24), 0.97),
            OcrBlock("Token: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9", (40, 120, 360, 24), 0.99),
            OcrBlock("Status: OK (200) - Benign Public Log", (40, 160, 280, 24), 0.99),
        ]


class TesseractOcrEngine(BaseOcrEngine):
    name: str = "tesseract"

    def detect_blocks(self, image_path: str, verbose: bool = False) -> List[OcrBlock]:
        """Run tesseract binary in TSV output mode to extract word and line bounding rects."""
        tess_bin = shutil.which("tesseract")
        if not tess_bin:
            raise RuntimeError("Tesseract binary not installed in PATH")

        cmd = [tess_bin, image_path, "stdout", "tsv"]
        if verbose:
            sys.stderr.write(f"[ocr-mask:tesseract] Running {' '.join(cmd)}\n")
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise RuntimeError(f"Tesseract OCR failed (exit {proc.returncode}): {proc.stderr}")

        blocks: List[OcrBlock] = []
        lines_map: Dict[Tuple[str, str, str, str], List[Dict[str, Any]]] = {}

        for line in proc.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) < 12 or parts[0] == "level":
                continue
            level, page, block_num, par_num, line_num, word_num = parts[0:6]
            left, top, width, height, conf = int(parts[6]), int(parts[7]), int(parts[8]), int(parts[9]), float(parts[10])
            text = parts[11].strip()
            if not text:
                continue

            if level == "5":  # Word level
                key = (page, block_num, par_num, line_num)
                lines_map.setdefault(key, []).append({
                    "text": text, "x": left, "y": top, "w": width, "h": height, "conf": conf / 100.0,
                })
                blocks.append(OcrBlock(text=text, bbox=(left, top, width, height), confidence=max(0.1, conf / 100.0)))

        # Group words into full text lines for multi-word credential regex matching
        for words in lines_map.values():
            if not words:
                continue
            joined_text = " ".join(w["text"] for w in words)
            min_x = min(w["x"] for w in words)
            min_y = min(w["y"] for w in words)
            max_x = max(w["x"] + w["w"] for w in words)
            max_y = max(w["y"] + w["h"] for w in words)
            avg_conf = sum(w["conf"] for w in words) / len(words)
            blocks.append(OcrBlock(
                text=joined_text,
                bbox=(min_x, min_y, max_x - min_x, max_y - min_y),
                confidence=avg_conf,
            ))

        return blocks


class PaddleOcrEngine(BaseOcrEngine):
    name: str = "paddleocr"

    def detect_blocks(self, image_path: str, verbose: bool = False) -> List[OcrBlock]:
        if not HAS_PADDLEOCR:
            raise RuntimeError("PaddleOCR library not installed")
        ocr = PaddleOCR(use_angle_cls=True, lang="en", show_log=verbose)
        results = ocr.ocr(image_path, cls=True)
        blocks = []
        for line in results[0]:
            box, (text, conf) = line
            # box is [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]
            xs = [pt[0] for pt in box]
            ys = [pt[1] for pt in box]
            min_x, min_y = int(min(xs)), int(min(ys))
            max_x, max_y = int(max(xs)), int(max(ys))
            blocks.append(OcrBlock(
                text=text,
                bbox=(min_x, min_y, max_x - min_x, max_y - min_y),
                confidence=float(conf),
            ))
        return blocks


def get_ocr_engine(force_mock: bool = False) -> BaseOcrEngine:
    """Resolve active OCR backend based on environment, flags, and system binaries."""
    if force_mock or os.environ.get("MIOS_OCR_MOCK", "").lower() in ("1", "true", "yes"):
        return MockOcrEngine()
    if shutil.which("tesseract"):
        return TesseractOcrEngine()
    if HAS_PADDLEOCR:
        return PaddleOcrEngine()
    # Fallback to Mock if no native engine is available
    return MockOcrEngine()


# ============================================================================
# Scanning and Redaction Pipeline
# ============================================================================

class CredentialMasker:
    """Orchestrates image loading, OCR text extraction, regex credential auditing, and masking."""

    def __init__(self, rules: Optional[List[CredentialRule]] = None, engine: Optional[BaseOcrEngine] = None):
        self.rules = rules or RULES
        self.engine = engine or get_ocr_engine()

    def audit_text(self, text: str, bbox: Tuple[int, int, int, int], confidence: float = 0.95) -> List[DetectedCredential]:
        """Scan a single text string across all active credential rules."""
        detected = []
        for rule in self.rules:
            for match in rule.pattern.finditer(text):
                matched_val = match.group(0)
                if rule.validator and not rule.validator(matched_val):
                    continue
                preview = rule.redactor(matched_val) if rule.redactor else "********"
                detected.append(DetectedCredential(
                    rule_id=rule.rule_id,
                    pattern_type=rule.name,
                    category=rule.category,
                    bbox=bbox,
                    matched_text=matched_val,
                    redacted_preview=preview,
                    confidence=confidence,
                ))
        return detected

    def scan_image(self, image_path: str, verbose: bool = False) -> Dict[str, Any]:
        """Audit an image for visible credentials and return detection coordinates."""
        frame = VisionFrame.load(image_path)
        blocks = self.engine.detect_blocks(image_path, verbose=verbose)

        all_detected: List[DetectedCredential] = []
        for block in blocks:
            creds = self.audit_text(block.text, block.bbox, block.confidence)
            all_detected.extend(creds)

        return {
            "status": "success",
            "image": os.path.abspath(image_path),
            "dimensions": {"width": frame.width, "height": frame.height},
            "ocr_backend": self.engine.name,
            "detected_count": len(all_detected),
            "credentials": [c.to_dict() for c in all_detected],
        }

    def mask_image(
        self,
        input_path: str,
        output_path: Optional[str] = None,
        dry_run: bool = False,
        verbose: bool = False,
    ) -> Dict[str, Any]:
        """Scan image, apply solid black masks over detected credential rects, and save masked output."""
        frame = VisionFrame.load(input_path)
        blocks = self.engine.detect_blocks(input_path, verbose=verbose)

        all_detected: List[DetectedCredential] = []
        for block in blocks:
            creds = self.audit_text(block.text, block.bbox, block.confidence)
            all_detected.extend(creds)

        boxes_masked = []
        if not dry_run and output_path:
            for cred in all_detected:
                x, y, w, h = cred.bbox
                frame.mask_rect(x, y, w, h, color=(0, 0, 0), margin=2)
                boxes_masked.append(list(cred.bbox))
            frame.save(output_path)
        else:
            boxes_masked = [list(c.bbox) for c in all_detected]

        return {
            "status": "success",
            "input": os.path.abspath(input_path),
            "output": os.path.abspath(output_path) if output_path and not dry_run else None,
            "dry_run": dry_run,
            "redacted_count": len(all_detected),
            "boxes_masked": boxes_masked,
            "credentials": [c.to_dict() for c in all_detected],
        }


# ============================================================================
# CLI Entrypoint & Subcommands
# ============================================================================

def cmd_status(args: argparse.Namespace) -> int:
    """Report OCR engine backend status, available engines, and registered credential rules."""
    engine = get_ocr_engine(force_mock=args.mock)
    payload = {
        "status": "ok",
        "ocr_backend": engine.name,
        "tesseract_available": shutil.which("tesseract") is not None,
        "paddleocr_available": HAS_PADDLEOCR,
        "pil_available": HAS_PIL,
        "rules_count": len(RULES),
        "rules": [
            {
                "id": r.rule_id,
                "name": r.name,
                "category": r.category,
                "description": r.description,
            }
            for r in RULES
        ],
    }
    print(json.dumps(payload, indent=2))
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    """Execute scan subcommand."""
    if not args.input:
        sys.stderr.write("Error: --input <image> is required for scan\n")
        return 2

    try:
        engine = get_ocr_engine(force_mock=args.mock)
        masker = CredentialMasker(engine=engine)
        res = masker.scan_image(args.input, verbose=args.verbose)
        print(json.dumps(res, indent=2))
        return 0
    except Exception as e:
        sys.stderr.write(f"Error: {e}\n")
        return 1


def cmd_mask(args: argparse.Namespace) -> int:
    """Execute mask subcommand."""
    if not args.input:
        sys.stderr.write("Error: --input <image> is required for mask\n")
        return 2
    if not args.output and not args.dry_run:
        sys.stderr.write("Error: --output <image> is required unless --dry-run is specified\n")
        return 2

    try:
        engine = get_ocr_engine(force_mock=args.mock)
        masker = CredentialMasker(engine=engine)
        res = masker.mask_image(
            input_path=args.input,
            output_path=args.output,
            dry_run=args.dry_run,
            verbose=args.verbose,
        )
        print(json.dumps(res, indent=2))
        return 0
    except Exception as e:
        sys.stderr.write(f"Error: {e}\n")
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="MiOS Lightweight On-Device OCR Regex Credential Masking Pipeline (task_484, task_2717)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose diagnostics to stderr")
    parser.add_argument("--mock", action="store_true", help="Force mock OCR engine backend")
    parser.add_argument("--dry-run", action="store_true", help="Simulate redactions without writing output files")

    subparsers = parser.add_subparsers(dest="command", help="Subcommand to execute")

    # scan
    p_scan = subparsers.add_parser("scan", help="Scan vision frame and output detected credential coordinates as JSON")
    p_scan.add_argument("--input", required=True, help="Input image file path")
    p_scan.add_argument("--mock", action="store_true", help="Force mock OCR engine backend")
    p_scan.add_argument("--dry-run", action="store_true", help="Dry run mode")
    p_scan.add_argument("-v", "--verbose", action="store_true", help="Verbose output")

    # mask
    p_mask = subparsers.add_parser("mask", help="Mask detected credentials with black-box redactions")
    p_mask.add_argument("--input", required=True, help="Input image file path")
    p_mask.add_argument("--output", help="Output masked image file path (required unless --dry-run)")
    p_mask.add_argument("--mock", action="store_true", help="Force mock OCR engine backend")
    p_mask.add_argument("--dry-run", action="store_true", help="Dry run mode (do not write output file)")
    p_mask.add_argument("-v", "--verbose", action="store_true", help="Verbose output")

    # status
    p_status = subparsers.add_parser("status", help="Report OCR engine status, backends, and active rules")
    p_status.add_argument("--mock", action="store_true", help="Report mock status")
    p_status.add_argument("-v", "--verbose", action="store_true", help="Verbose output")

    return parser


def main() -> int:
    parser = build_parser()
    if len(sys.argv) <= 1:
        parser.print_help()
        return 0

    args = parser.parse_args()

    # Propagate top-level flags down to subcommands if present
    if hasattr(args, "mock") and not args.mock and parser.parse_known_args()[0].mock:
        args.mock = True

    if args.command == "status":
        return cmd_status(args)
    elif args.command == "scan":
        return cmd_scan(args)
    elif args.command == "mask":
        return cmd_mask(args)
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
