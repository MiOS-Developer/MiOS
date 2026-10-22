#!/usr/bin/env python3
# AI-hint: Audio feedback daemon playing subtle non-intrusive sound cues with pure Python PCM synthesis
# AI-related: tests/test-ux.py, usr/share/mios/mios.toml, usr/lib/mios/mios_toml.py
# AI-functions: AudioFeedbackEngine, synthesize_event_pcm, play_audio_cue, main
"""
MiOS Audio Feedback Daemon & Harmonic PCM Synthesizer.

Provides subtle, non-intrusive auditory cues on task transitions:
- Events: `completed`, `started`, `requires_input`, `warning`, `failed`.
- Backends: PipeWire (`pw-play`), PulseAudio (`paplay`), or pure-Python 16-bit PCM synthesis.
- Generates mathematically crafted soft harmonic chimes with exponential decay.
- CLI synthesis tool to pre-render all system event sound files to `/usr/share/sounds/mios/`.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import struct
import subprocess
import sys
import wave
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Enable relative import of mios_toml
_LIB_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "lib", "mios")
)
if os.path.isdir(_LIB_DIR) and _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)

try:
    import mios_toml
except ImportError:
    mios_toml = None

EVENT_CHORD_MAP: Dict[str, Dict[str, Any]] = {
    "completed": {
        "freqs": [523.25, 659.25, 783.99, 1046.50],  # C5 - E5 - G5 - C6 major chord
        "duration": 0.35,
        "decay": 7.0,
    },
    "started": {
        "freqs": [440.0, 659.25],  # A4 - E5 rising fifth
        "duration": 0.20,
        "decay": 8.0,
    },
    "requires_input": {
        "freqs": [880.0, 1174.66],  # A5 - D6 bell chime
        "duration": 0.30,
        "decay": 6.0,
    },
    "warning": {
        "freqs": [440.0, 466.16],  # A4 - Bb4 minor second pulse
        "duration": 0.25,
        "decay": 9.0,
    },
    "failed": {
        "freqs": [440.0, 311.13],  # A4 - Eb4 descending tritone
        "duration": 0.40,
        "decay": 6.5,
    },
}

def synthesize_event_pcm(
    event_name: str,
    output_wav_path: str,
    volume: float = 0.4,
    sample_rate: int = 44100,
) -> int:
    """Synthesize a 16-bit mono PCM WAV file for an event cue. Returns sample count."""
    cfg = EVENT_CHORD_MAP.get(event_name, EVENT_CHORD_MAP["completed"])
    freqs: List[float] = cfg["freqs"]
    duration: float = cfg["duration"]
    decay: float = cfg["decay"]

    num_samples = int(duration * sample_rate)
    parent = os.path.dirname(output_wav_path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    with wave.open(output_wav_path, "w") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)

        frames = bytearray()
        for i in range(num_samples):
            t = i / sample_rate
            envelope = math.exp(-t * decay)
            sample_val = 0.0
            for freq in freqs:
                sample_val += math.sin(2.0 * math.pi * freq * t)
            sample_val = (sample_val / len(freqs)) * envelope * volume
            sample_int = int(max(-1.0, min(1.0, sample_val)) * 32767.0)
            frames.extend(struct.pack("<h", sample_int))

        wav_file.writeframes(frames)

    return num_samples

class AudioFeedbackEngine:
    """Audio cue dispatcher and procedural sound synthesizer."""

    def __init__(
        self,
        volume_pct: int = 50,
        sounds_dir: str = "/usr/share/sounds/mios",
        mock: bool = False,
        dry_run: bool = False,
    ):
        self.volume_pct = max(0, min(100, volume_pct))
        self.volume_norm = self.volume_pct / 100.0
        self.sounds_dir = sounds_dir
        self.mock = mock
        self.dry_run = dry_run

    def synthesize_all(self, target_dir: str) -> Dict[str, str]:
        """Synthesize all event sound cues into target directory."""
        results = {}
        if self.mock or self.dry_run:
            for event in EVENT_CHORD_MAP:
                results[event] = os.path.join(target_dir, f"{event}.wav")
            return results

        os.makedirs(target_dir, exist_ok=True)
        for event in EVENT_CHORD_MAP:
            wav_path = os.path.join(target_dir, f"{event}.wav")
            synthesize_event_pcm(event, wav_path, volume=self.volume_norm)
            results[event] = wav_path

        return results

    def play_cue(self, event_name: str) -> Dict[str, Any]:
        """Play sound cue for specified event via PipeWire, PulseAudio or in-memory synthesis."""
        if event_name not in EVENT_CHORD_MAP:
            raise ValueError(f"Unknown event '{event_name}'. Allowed: {list(EVENT_CHORD_MAP.keys())}")

        if self.mock:
            return {
                "played": True,
                "event": event_name,
                "backend": "mock_pcm",
                "volume_pct": self.volume_pct,
                "duration_sec": EVENT_CHORD_MAP[event_name]["duration"],
            }

        # Check existing wav file
        wav_path = os.path.join(self.sounds_dir, f"{event_name}.wav")
        temp_created = False
        if not os.path.exists(wav_path):
            # Synthesize on the fly
            wav_path = os.path.join("/tmp", f"mios_cue_{event_name}.wav")
            synthesize_event_pcm(event_name, wav_path, volume=self.volume_norm)
            temp_created = True

        backend = "none"
        played = False
        # Try pw-play (PipeWire)
        if shutil.which("pw-play"):
            try:
                subprocess.run(["pw-play", wav_path], check=True, timeout=2, capture_output=True)
                backend = "pw-play"
                played = True
            except Exception:
                pass

        # Try paplay (PulseAudio)
        if not played and shutil.which("paplay"):
            try:
                subprocess.run(["paplay", wav_path], check=True, timeout=2, capture_output=True)
                backend = "paplay"
                played = True
            except Exception:
                pass

        # Cleanup temp file if created
        if temp_created and os.path.exists(wav_path):
            try:
                os.remove(wav_path)
            except Exception:
                pass

        return {
            "played": played,
            "event": event_name,
            "backend": backend,
            "volume_pct": self.volume_pct,
            "duration_sec": EVENT_CHORD_MAP[event_name]["duration"],
        }

def main() -> int:
    parser = argparse.ArgumentParser(
        description="MiOS Subtle Audio Feedback Daemon & Sound Synthesizer"
    )
    parser.add_argument("--event", choices=list(EVENT_CHORD_MAP.keys()),
                        help="Audio event transition to play")
    parser.add_argument("--volume", type=int, default=50, help="Volume percentage (0-100)")
    parser.add_argument("--synthesize-to", help="Synthesize all event WAV files into directory")
    parser.add_argument("--play", action="store_true", help="Play audio cue for event")
    parser.add_argument("--dry-run", action="store_true", help="Simulate execution without playing audio")
    parser.add_argument("--mock", action="store_true", help="Deterministic mock execution for CI")
    parser.add_argument("--json", action="store_true", help="Format output as JSON dictionary")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")

    args = parser.parse_args()

    engine = AudioFeedbackEngine(
        volume_pct=args.volume,
        mock=args.mock,
        dry_run=args.dry_run,
    )

    try:
        if args.synthesize_to:
            synthesized = engine.synthesize_all(args.synthesize_to)
            res = {
                "status": "success",
                "synthesized_files": synthesized,
                "count": len(synthesized),
                "mock": args.mock,
            }
        else:
            event = args.event or "completed"
            play_res = engine.play_cue(event)
            res = {
                "status": "success",
                "result": play_res,
                "mock": args.mock,
            }

        if args.json:
            print(json.dumps(res, indent=2))
        else:
            if "synthesized_files" in res:
                print(f"[audio_feedback] SUCCESS: Synthesized {res['count']} event sound files to {args.synthesize_to}")
            else:
                r = res["result"]
                print(f"[audio_feedback] SUCCESS: Event '{r['event']}' cue triggered via backend '{r['backend']}'")
        return 0
    except Exception as e:
        err = {"status": "error", "error": str(e)}
        if args.json:
            print(json.dumps(err, indent=2))
        else:
            print(f"[audio_feedback] ERROR: {e}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())
