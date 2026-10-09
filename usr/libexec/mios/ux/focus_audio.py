#!/usr/bin/env python3
# AI-hint: Offline procedural ambient background audio synthesizer for deep focus programming sessions.
# AI-related: tests/test-ux.py, usr/share/mios/mios.toml
# AI-functions: FocusAudioSynthesizer, FocusPreset, main
"""
MiOS Offline Procedural Focus Audio Synthesizer (task_408).

Procedurally synthesizes 100% offline ambient soundscapes for deep focus programming:
- Pink noise (1/f spectral density via Voss-McCartney algorithm).
- Brown noise (1/f^2 random walk / leaky integrator).
- White noise (uniform stochastic).
- Rain (filtered droplet burst simulation).
- Ocean (slow sinusoidal low-frequency wave modulation).
- Binaural Alpha (10Hz frequency offset for relaxed focus).
- Binaural Theta (6Hz frequency offset for deep flow state).

Pure Python standard library implementation with zero cloud streaming or external dependencies.
Streams directly to PipeWire (pw-play) or exports standard 16-bit PCM WAV files.
"""

from __future__ import annotations

import argparse
import io
import json
import math
import os
import random
import shutil
import struct
import subprocess
import sys
import wave
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_LIB_PATH = os.path.normpath(os.path.join(_HERE, "..", "..", "..", "lib", "mios"))
if os.path.isdir(_LIB_PATH) and _LIB_PATH not in sys.path:
    sys.path.insert(0, _LIB_PATH)

try:
    import mios_toml
except ImportError:
    mios_toml = None

DEFAULT_SAMPLE_RATE = 44100
DEFAULT_DURATION_SEC = 5.0
DEFAULT_VOLUME = 70

@dataclass
class FocusPreset:
    """Descriptor for an ambient focus soundscape preset."""
    name: str
    description: str
    channels: int  # 1 for mono, 2 for stereo (binaural)
    frequency_range: str
    recommended_use: str

AVAILABLE_PRESETS: Dict[str, FocusPreset] = {
    "pink_noise": FocusPreset(
        name="pink_noise",
        description="1/f spectral density with equal energy per octave; balanced rushing sound.",
        channels=1,
        frequency_range="20Hz - 20kHz",
        recommended_use="General programming, reading documentation, masking office chatter.",
    ),
    "brown_noise": FocusPreset(
        name="brown_noise",
        description="1/f^2 random walk leaky integrator; deep soothing low-frequency rumble.",
        channels=1,
        frequency_range="20Hz - 1kHz",
        recommended_use="Deep analytical debugging, algorithm design, sleep aid.",
    ),
    "white_noise": FocusPreset(
        name="white_noise",
        description="Uniform power across all frequencies; crisp waterfall acoustic profile.",
        channels=1,
        frequency_range="20Hz - 20kHz",
        recommended_use="High-intensity masking of abrupt background noises.",
    ),
    "rain": FocusPreset(
        name="rain",
        description="Filtered Brownian baseline overlaid with stochastic droplet splashes.",
        channels=1,
        frequency_range="100Hz - 8kHz",
        recommended_use="Creative coding, refactoring sessions, extended pair-programming.",
    ),
    "ocean": FocusPreset(
        name="ocean",
        description="Sinusoidal modulated pink noise mimicking rhythmic ocean waves (~8s cycle).",
        channels=1,
        frequency_range="40Hz - 4kHz",
        recommended_use="Relaxed exploratory programming and system design.",
    ),
    "binaural_alpha": FocusPreset(
        name="binaural_alpha",
        description="Stereo sine tones with 10Hz offset (210Hz left / 220Hz right).",
        channels=2,
        frequency_range="210Hz / 220Hz",
        recommended_use="Alertness, memory recall, active problem solving (Headphones Required).",
    ),
    "binaural_theta": FocusPreset(
        name="binaural_theta",
        description="Stereo sine tones with 6Hz offset (194Hz left / 200Hz right).",
        channels=2,
        frequency_range="194Hz / 200Hz",
        recommended_use="Deep flow state, complex mathematical reasoning (Headphones Required).",
    ),
}

class FocusAudioSynthesizer:
    """Generates procedural PCM audio buffers for focus soundscapes."""

    def __init__(
        self,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        mock: bool = False,
        dry_run: bool = False,
        verbose: bool = False,
    ) -> None:
        self.sample_rate = sample_rate
        self.mock = mock
        self.dry_run = dry_run
        self.verbose = verbose

    def _synth_white_noise(self, num_samples: int) -> List[float]:
        """Generate white noise samples."""
        return [random.uniform(-1.0, 1.0) for _ in range(num_samples)]

    def _synth_pink_noise(self, num_samples: int) -> List[float]:
        """Generate pink noise using 5-pole filter approximation."""
        b0 = b1 = b2 = b3 = b4 = b5 = b6 = 0.0
        samples = []
        for _ in range(num_samples):
            white = random.uniform(-1.0, 1.0)
            b0 = 0.99886 * b0 + white * 0.0555179
            b1 = 0.99332 * b1 + white * 0.0750759
            b2 = 0.96900 * b2 + white * 0.1538520
            b3 = 0.86650 * b3 + white * 0.3104856
            b4 = 0.55000 * b4 + white * 0.5329522
            b5 = -0.7616 * b5 - white * 0.0168980
            pink = b0 + b1 + b2 + b3 + b4 + b5 + b6 + white * 0.5362
            b6 = white * 0.115926
            samples.append(pink * 0.15)  # Scale to roughly [-1.0, 1.0]
        return samples

    def _synth_brown_noise(self, num_samples: int) -> List[float]:
        """Generate brown noise using a leaky integrator random walk."""
        last_val = 0.0
        samples = []
        for _ in range(num_samples):
            white = random.uniform(-1.0, 1.0)
            last_val = (last_val * 0.95) + (white * 0.05)
            samples.append(last_val * 3.5)
        return samples

    def _synth_rain(self, num_samples: int) -> List[float]:
        """Generate rain simulation."""
        brown = self._synth_brown_noise(num_samples)
        samples = []
        for i in range(num_samples):
            base = brown[i] * 0.4
            # Stochastic high-frequency droplet bursts
            if random.random() < 0.015:
                droplet = random.uniform(-0.6, 0.6)
            else:
                droplet = 0.0
            samples.append(base + droplet)
        return samples

    def _synth_ocean(self, num_samples: int) -> List[float]:
        """Generate ocean wave soundscape with slow sinusoidal modulation."""
        pink = self._synth_pink_noise(num_samples)
        wave_period_samples = int(self.sample_rate * 8.0)  # 8 second wave cycle
        samples = []
        for i in range(num_samples):
            phase = (i % wave_period_samples) / wave_period_samples
            envelope = 0.25 + 0.75 * ((1.0 + math.sin(2.0 * math.pi * phase)) / 2.0)
            samples.append(pink[i] * envelope)
        return samples

    def _synth_binaural(self, num_samples: int, f_left: float, f_right: float) -> Tuple[List[float], List[float]]:
        """Generate stereo binaural beat tones."""
        left = []
        right = []
        for i in range(num_samples):
            t = i / self.sample_rate
            left.append(math.sin(2.0 * math.pi * f_left * t) * 0.5)
            right.append(math.sin(2.0 * math.pi * f_right * t) * 0.5)
        return left, right

    def synthesize_pcm(
        self,
        preset_name: str,
        duration_sec: float = DEFAULT_DURATION_SEC,
        volume_pct: int = DEFAULT_VOLUME,
    ) -> Tuple[bytes, int]:
        """
        Synthesize raw 16-bit signed PCM audio bytes.
        Returns: (pcm_bytes, channel_count)
        """
        if preset_name not in AVAILABLE_PRESETS:
            raise ValueError(f"Unknown preset: {preset_name}. Available: {list(AVAILABLE_PRESETS.keys())}")

        preset = AVAILABLE_PRESETS[preset_name]
        num_samples = int(self.sample_rate * duration_sec)
        vol_scale = max(0.0, min(1.0, volume_pct / 100.0)) * 32767.0

        if self.mock:
            # Deterministic synthetic PCM frames for testing
            channels = preset.channels
            total_samples = num_samples * channels
            # Generate deterministic sine waveform
            raw_frames = bytearray(total_samples * 2)
            for i in range(total_samples):
                sample_val = int(math.sin(2 * math.pi * 440.0 * (i / self.sample_rate)) * vol_scale * 0.5)
                struct.pack_into("<h", raw_frames, i * 2, max(-32768, min(32767, sample_val)))
            return bytes(raw_frames), channels

        channels = preset.channels
        if preset_name == "binaural_alpha":
            left_f, right_f = self._synth_binaural(num_samples, 210.0, 220.0)
            raw_frames = bytearray(num_samples * 4)  # 2 channels * 2 bytes
            for i in range(num_samples):
                l_val = int(max(-1.0, min(1.0, left_f[i])) * vol_scale)
                r_val = int(max(-1.0, min(1.0, right_f[i])) * vol_scale)
                struct.pack_into("<h", raw_frames, i * 4, l_val)
                struct.pack_into("<h", raw_frames, i * 4 + 2, r_val)
            return bytes(raw_frames), 2

        elif preset_name == "binaural_theta":
            left_f, right_f = self._synth_binaural(num_samples, 194.0, 200.0)
            raw_frames = bytearray(num_samples * 4)
            for i in range(num_samples):
                l_val = int(max(-1.0, min(1.0, left_f[i])) * vol_scale)
                r_val = int(max(-1.0, min(1.0, right_f[i])) * vol_scale)
                struct.pack_into("<h", raw_frames, i * 4, l_val)
                struct.pack_into("<h", raw_frames, i * 4 + 2, r_val)
            return bytes(raw_frames), 2

        # Mono generators
        if preset_name == "white_noise":
            samples = self._synth_white_noise(num_samples)
        elif preset_name == "pink_noise":
            samples = self._synth_pink_noise(num_samples)
        elif preset_name == "brown_noise":
            samples = self._synth_brown_noise(num_samples)
        elif preset_name == "rain":
            samples = self._synth_rain(num_samples)
        elif preset_name == "ocean":
            samples = self._synth_ocean(num_samples)
        else:
            samples = self._synth_pink_noise(num_samples)

        raw_frames = bytearray(num_samples * 2)
        for i in range(num_samples):
            clamped = max(-1.0, min(1.0, samples[i]))
            val = int(clamped * vol_scale)
            struct.pack_into("<h", raw_frames, i * 2, val)

        return bytes(raw_frames), 1

    def export_wav(
        self,
        preset_name: str,
        out_path: str,
        duration_sec: float = DEFAULT_DURATION_SEC,
        volume_pct: int = DEFAULT_VOLUME,
    ) -> Dict[str, Any]:
        """Synthesize and write a standard WAV audio file."""
        pcm_data, channels = self.synthesize_pcm(preset_name, duration_sec, volume_pct)

        if not self.mock and not self.dry_run:
            parent = os.path.dirname(out_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with wave.open(out_path, "wb") as wf:
                wf.setnchannels(channels)
                wf.setsampwidth(2)  # 16-bit
                wf.setframerate(self.sample_rate)
                wf.writeframes(pcm_data)

        return {
            "status": "success",
            "action": "export_wav",
            "preset": preset_name,
            "output_path": out_path,
            "duration_sec": duration_sec,
            "volume_pct": volume_pct,
            "channels": channels,
            "sample_rate": self.sample_rate,
            "pcm_bytes": len(pcm_data),
            "dry_run": self.dry_run,
            "mock": self.mock,
        }

    def play(
        self,
        preset_name: str,
        duration_sec: float = DEFAULT_DURATION_SEC,
        volume_pct: int = DEFAULT_VOLUME,
    ) -> Dict[str, Any]:
        """Synthesize and stream audio to PipeWire or system audio player."""
        pcm_data, channels = self.synthesize_pcm(preset_name, duration_sec, volume_pct)

        backend = "mock"
        if not self.mock and not self.dry_run:
            if shutil.which("pw-play"):
                backend = "pw-play"
                try:
                    # Stream raw 16-bit PCM via stdin
                    cmd = ["pw-play", "--rate", str(self.sample_rate), "--channels", str(channels), "--format", "s16", "-"]
                    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
                    proc.communicate(input=pcm_data)
                except Exception as e:
                    return {"status": "error", "error": f"pw-play execution failed: {e}"}
            elif shutil.which("paplay"):
                backend = "paplay"
                try:
                    cmd = ["paplay", "--raw", f"--rate={self.sample_rate}", f"--channels={channels}", "--format=s16le"]
                    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
                    proc.communicate(input=pcm_data)
                except Exception as e:
                    return {"status": "error", "error": f"paplay execution failed: {e}"}
            else:
                backend = "none_available"

        return {
            "status": "success",
            "action": "play",
            "preset": preset_name,
            "backend": backend,
            "duration_sec": duration_sec,
            "volume_pct": volume_pct,
            "channels": channels,
            "sample_rate": self.sample_rate,
            "pcm_bytes": len(pcm_data),
            "dry_run": self.dry_run,
            "mock": self.mock,
        }

def main() -> int:
    parser = argparse.ArgumentParser(
        description="MiOS Offline Procedural Focus Audio Synthesizer (task_408)"
    )
    parser.add_argument(
        "--preset",
        choices=list(AVAILABLE_PRESETS.keys()),
        default="pink_noise",
        help="Ambient audio preset soundscape",
    )
    parser.add_argument("--list-presets", action="store_true", help="List all available procedural soundscape presets")
    parser.add_argument("--duration", type=float, default=DEFAULT_DURATION_SEC, help="Duration in seconds")
    parser.add_argument("--volume", type=int, default=DEFAULT_VOLUME, help="Volume percentage (0-100)")
    parser.add_argument("--out", help="Output .wav file path")
    parser.add_argument("--play", action="store_true", help="Stream audio to PipeWire / speaker output")
    parser.add_argument("--mock", action="store_true", help="Deterministic in-memory mock mode")
    parser.add_argument("--dry-run", action="store_true", help="Simulate synthesis without audio output")
    parser.add_argument("--json", action="store_true", help="Emit output as JSON")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose output")

    args = parser.parse_args()

    synth = FocusAudioSynthesizer(
        mock=args.mock,
        dry_run=args.dry_run,
        verbose=args.verbose,
    )

    try:
        if args.list_presets:
            result = {
                "status": "success",
                "presets_count": len(AVAILABLE_PRESETS),
                "presets": {k: asdict(v) for k, v in AVAILABLE_PRESETS.items()},
            }
        elif args.out:
            result = synth.export_wav(
                preset_name=args.preset,
                out_path=args.out,
                duration_sec=args.duration,
                volume_pct=args.volume,
            )
        elif args.play:
            result = synth.play(
                preset_name=args.preset,
                duration_sec=args.duration,
                volume_pct=args.volume,
            )
        else:
            # Default action: dry synthesis check
            pcm, ch = synth.synthesize_pcm(args.preset, duration_sec=args.duration, volume_pct=args.volume)
            result = {
                "status": "success",
                "action": "synthesize",
                "preset": args.preset,
                "duration_sec": args.duration,
                "volume_pct": args.volume,
                "channels": ch,
                "pcm_bytes": len(pcm),
                "mock": args.mock,
            }

        if args.json:
            print(json.dumps(result, indent=2))
        else:
            status = result.get("status", "ok")
            print(f"[focus_audio] Status: {status}")
            if "presets" in result:
                for k, v in result["presets"].items():
                    print(f"  - {k}: {v['description']} ({v['recommended_use']})")
            elif "output_path" in result:
                print(f"  Exported WAV: {result['output_path']} ({result['pcm_bytes']} bytes)")
            elif "backend" in result:
                print(f"  Playing '{result['preset']}' via {result['backend']} for {result['duration_sec']}s")
            else:
                print(f"  Synthesized {result.get('pcm_bytes', 0)} bytes of '{result.get('preset')}' audio")
        return 0
    except Exception as e:
        err = {"status": "error", "error": str(e)}
        if args.json:
            print(json.dumps(err, indent=2))
        else:
            print(f"[focus_audio] ERROR: {e}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())
