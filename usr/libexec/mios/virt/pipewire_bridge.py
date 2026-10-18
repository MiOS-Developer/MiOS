#!/usr/bin/env python3
# AI-hint: Low-latency PipeWire JACK / PulseAudio inter-VM audio bridge with Scream IVSHMEM sink.
# AI-related: tests/test-virt.py, usr/share/doc/mios/manual/ch67-discrete-gpu-vfio-looking-glass-and-displays.md
"""
MiOS Inter-VM PipeWire Low-Latency Audio Bridge.

Manages Scream IVSHMEM audio sinks, calculates buffer latency math enforcing
sub-5ms SLA (e.g. 64/48000 = 1.33ms), generates libvirt domain IVSHMEM XML snippets,
synthesizes systemd service units, and configures PipeWire JACK environment overrides.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_SHM_PATH = "/dev/shm/scream"
DEFAULT_SHM_SIZE_MB = 2
DEFAULT_SAMPLE_RATE = 48000
DEFAULT_QUANTUM = 64
DEFAULT_BACKEND = "jack"
DEFAULT_NODE_NAME = "scream-ivshmem-bridge"
SLA_LATENCY_MAX_MS = 5.0

SUPPORTED_RATES: Tuple[int, ...] = (44100, 48000, 88200, 96000, 176400, 192000)
SUPPORTED_QUANTUMS: Tuple[int, ...] = (32, 64, 128, 256, 512, 1024)

class PipeWireBridgeManager:
    """Manages low-latency inter-VM Scream IVSHMEM to PipeWire JACK audio bridge."""

    def __init__(
        self,
        shm_path: str = DEFAULT_SHM_PATH,
        size_mb: int = DEFAULT_SHM_SIZE_MB,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        quantum: int = DEFAULT_QUANTUM,
        backend: str = DEFAULT_BACKEND,
        node_name: str = DEFAULT_NODE_NAME,
        max_sla_ms: float = SLA_LATENCY_MAX_MS,
    ) -> None:
        self.shm_path = shm_path
        self.size_mb = size_mb
        self.sample_rate = sample_rate
        self.quantum = quantum
        self.backend = backend.lower()
        self.node_name = node_name
        self.max_sla_ms = max_sla_ms

    @staticmethod
    def calculate_latency_ms(quantum: int, sample_rate: int) -> float:
        """Calculates audio buffer latency in milliseconds: (quantum / sample_rate) * 1000."""
        if sample_rate <= 0:
            raise ValueError(f"Sample rate must be positive, got {sample_rate}")
        if quantum <= 0:
            raise ValueError(f"Quantum must be positive, got {quantum}")
        raw_ms = (quantum / sample_rate) * 1000.0
        return round(raw_ms, 3)

    def get_latency_ms(self) -> float:
        """Returns buffer latency for the current instance configuration."""
        return self.calculate_latency_ms(self.quantum, self.sample_rate)

    def validate_latency_sla(
        self,
        quantum: Optional[int] = None,
        sample_rate: Optional[int] = None,
        max_sla_ms: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Validates whether quantum/sample_rate configuration satisfies the sub-5ms SLA."""
        q = quantum or self.quantum
        sr = sample_rate or self.sample_rate
        sla = max_sla_ms or self.max_sla_ms

        latency_ms = self.calculate_latency_ms(q, sr)
        passed = latency_ms <= sla

        return {
            "status": "pass" if passed else "fail",
            "latency_ms": latency_ms,
            "sla_max_ms": sla,
            "quantum": q,
            "sample_rate": sr,
            "passed": passed,
            "formula": f"({q} / {sr}) * 1000 = {latency_ms} ms",
        }

    def generate_pipewire_env(self) -> Dict[str, str]:
        """Generates PipeWire and JACK low-latency environment variables."""
        return {
            "PIPEWIRE_LATENCY": f"{self.quantum}/{self.sample_rate}",
            "PIPEWIRE_QUANTUM": f"{self.quantum}/{self.sample_rate}",
            "PIPEWIRE_RATE": f"1/{self.sample_rate}",
            "JACK_PROMISCUOUS_SERVER": "1",
            "PIPEWIRE_NODE_NAME": self.node_name,
        }

    def generate_ivshmem_xml(self, shmem_name: str = "scream") -> str:
        """Generates libvirt IVSHMEM domain XML snippet for Scream audio."""
        return f"""<shmem name="{shmem_name}">
  <model type="ivshmem-plain"/>
  <size unit="M">{self.size_mb}</size>
</shmem>"""

    def generate_systemd_service(
        self,
        user_unit: bool = False,
        scream_bin: str = "/usr/bin/scream",
    ) -> str:
        """Synthesizes systemd service unit configuration for the Scream IVSHMEM bridge."""
        env_lines = [
            f'Environment="PIPEWIRE_LATENCY={self.quantum}/{self.sample_rate}"',
            f'Environment="PIPEWIRE_QUANTUM={self.quantum}/{self.sample_rate}"',
            f'Environment="PIPEWIRE_RATE=1/{self.sample_rate}"',
            'Environment="JACK_PROMISCUOUS_SERVER=1"',
            f'Environment="PIPEWIRE_NODE_NAME={self.node_name}"',
        ]
        env_block = "\n".join(env_lines)
        target = "default.target" if user_unit else "multi-user.target"

        return f"""[Unit]
Description=MiOS Scream IVSHMEM to PipeWire {self.backend.upper()} Low-Latency Audio Bridge
After=pipewire.service
Requires=pipewire.service

[Service]
Type=simple
{env_block}
ExecStart={scream_bin} -m {self.shm_path} -o {self.backend} -t {self.quantum}
Restart=always
RestartSec=2
LimitRTPRIO=95
LimitMEMLOCK=infinity

[Install]
WantedBy={target}
"""

    def validate_audio_nodes(self, mock: bool = False) -> Dict[str, Any]:
        """Validates IVSHMEM audio node existence and permissions."""
        if mock or os.name == "nt":
            return {
                "status": "pass",
                "shm_path": self.shm_path,
                "accessible": True,
                "mock": True,
            }

        if not os.path.exists(self.shm_path):
            return {
                "status": "fail",
                "shm_path": self.shm_path,
                "error": f"IVSHMEM audio sink {self.shm_path} not found",
                "mock": False,
            }

        st = os.stat(self.shm_path)
        mode_ok = (st.st_mode & 0o777) == 0o660
        return {
            "status": "pass" if mode_ok else "fail",
            "shm_path": self.shm_path,
            "accessible": mode_ok,
            "mode": oct(st.st_mode),
            "mock": False,
        }

    def verify_all(self, mock: bool = False) -> Dict[str, Any]:
        """Runs complete diagnostics across latency SLA, audio node validation, and XML/unit synthesis."""
        sla_res = self.validate_latency_sla()
        node_res = self.validate_audio_nodes(mock=mock)
        xml = self.generate_ivshmem_xml()
        env = self.generate_pipewire_env()
        service = self.generate_systemd_service()

        xml_valid = '<shmem name="scream">' in xml and f'<size unit="M">{self.size_mb}</size>' in xml
        env_valid = env.get("PIPEWIRE_LATENCY") == f"{self.quantum}/{self.sample_rate}"
        service_valid = f"-m {self.shm_path}" in service and f"-o {self.backend}" in service

        overall_pass = (
            sla_res["passed"]
            and (node_res["status"] == "pass" or mock or os.name == "nt")
            and xml_valid
            and env_valid
            and service_valid
        )

        return {
            "status": "pass" if overall_pass else "fail",
            "shm_path": self.shm_path,
            "size_mb": self.size_mb,
            "sample_rate": self.sample_rate,
            "quantum": self.quantum,
            "backend": self.backend,
            "latency_ms": sla_res["latency_ms"],
            "sla_passed": sla_res["passed"],
            "checks": {
                "latency_sla": "pass" if sla_res["passed"] else "fail",
                "audio_node": node_res["status"],
                "xml_generation": "pass" if xml_valid else "fail",
                "env_generation": "pass" if env_valid else "fail",
                "service_generation": "pass" if service_valid else "fail",
            },
            "mock": mock or os.name == "nt",
        }

def main() -> int:
    parser = argparse.ArgumentParser(
        description="MiOS Inter-VM PipeWire Low-Latency Audio Bridge Utility."
    )
    parser.add_argument("--shm-path", type=str, default=DEFAULT_SHM_PATH, help="Path to IVSHMEM Scream sink file.")
    parser.add_argument("--size-mb", type=int, default=DEFAULT_SHM_SIZE_MB, help="IVSHMEM buffer size in MB.")
    parser.add_argument("--sample-rate", type=int, default=DEFAULT_SAMPLE_RATE, help="Audio sample rate (Hz).")
    parser.add_argument("--quantum", type=int, default=DEFAULT_QUANTUM, help="PipeWire buffer quantum (samples).")
    parser.add_argument("--backend", type=str, default=DEFAULT_BACKEND, choices=["jack", "pulse"], help="Audio backend.")
    parser.add_argument("--node-name", type=str, default=DEFAULT_NODE_NAME, help="PipeWire node name.")
    parser.add_argument("--generate-xml", action="store_true", help="Generate libvirt domain IVSHMEM XML snippet.")
    parser.add_argument("--generate-service", action="store_true", help="Generate systemd service unit.")
    parser.add_argument("--user-unit", action="store_true", help="Generate systemd user unit instead of system.")
    parser.add_argument("--calc-latency", action="store_true", help="Calculate latency for quantum/sample_rate.")
    parser.add_argument("--sla-check", action="store_true", help="Check sub-5ms SLA compliance.")
    parser.add_argument("--verify", action="store_true", help="Run full diagnostic verification.")
    parser.add_argument("--output", type=str, default=None, help="Optional output file path.")
    parser.add_argument("--json", action="store_true", help="Output results in JSON format.")
    parser.add_argument("--mock", action="store_true", help="Run in synthetic mock mode.")
    args = parser.parse_args()

    manager = PipeWireBridgeManager(
        shm_path=args.shm_path,
        size_mb=args.size_mb,
        sample_rate=args.sample_rate,
        quantum=args.quantum,
        backend=args.backend,
        node_name=args.node_name,
    )

    result_text = ""
    json_data: Optional[Dict[str, Any]] = None

    if args.generate_xml:
        result_text = manager.generate_ivshmem_xml()
        if args.json:
            json_data = {"xml": result_text, "size_mb": args.size_mb, "shmem_name": "scream"}

    elif args.generate_service:
        result_text = manager.generate_systemd_service(user_unit=args.user_unit)
        if args.json:
            json_data = {
                "service": result_text,
                "backend": args.backend,
                "user_unit": args.user_unit,
                "env": manager.generate_pipewire_env(),
            }

    elif args.calc_latency or args.sla_check:
        sla_info = manager.validate_latency_sla()
        if args.json:
            json_data = sla_info
        else:
            result_text = (
                f"Latency: {sla_info['latency_ms']} ms "
                f"(quantum={sla_info['quantum']}, rate={sla_info['sample_rate']} Hz)\n"
                f"SLA: {'PASS' if sla_info['passed'] else 'FAIL'} (Threshold <= {sla_info['sla_max_ms']} ms)"
            )

    elif args.verify or not sys.argv[1:]:
        verify_results = manager.verify_all(mock=args.mock or os.name == "nt")
        if args.json:
            json_data = verify_results
        else:
            result_text = (
                f"[pipewire-bridge] Status: {verify_results['status'].upper()} (mock={verify_results['mock']})\n"
                f"  - Latency: {verify_results['latency_ms']} ms (SLA: {'PASS' if verify_results['sla_passed'] else 'FAIL'})\n"
                f"  - SHM Node: {verify_results['shm_path']} ({verify_results['checks']['audio_node']})\n"
                f"  - XML Gen: {verify_results['checks']['xml_generation']}\n"
                f"  - Service Gen: {verify_results['checks']['service_generation']}\n"
            )
        if not args.output and not args.json:
            sys.stdout.write(result_text)
            return 0 if verify_results["status"] == "pass" else 1

    if json_data is not None and args.json:
        result_text = json.dumps(json_data, indent=2) + "\n"

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(result_text)
    else:
        sys.stdout.write(result_text + ("\n" if not result_text.endswith("\n") else ""))

    return 0

if __name__ == "__main__":
    sys.exit(main())
