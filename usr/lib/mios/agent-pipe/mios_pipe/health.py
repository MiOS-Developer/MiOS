# AI-hint: Health and status endpoint response builder module for MiOS agent-pipe.
from __future__ import annotations

import os
from typing import Any, Dict, Optional

def get_system_version() -> str:
    if "MIOS_VERSION" in os.environ:
        return os.environ["MIOS_VERSION"]
    for path in ["/mnt/c/MiOS/VERSION", "C:\\MiOS\\VERSION", "/usr/share/mios/VERSION", "/etc/mios/VERSION"]:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    v = fh.read().strip()
                    if v:
                        return v
            except OSError:
                pass
    return "0.0.0"

def build_health_response(
    status: str = "ok",
    version: Optional[str] = None,
    backend: Optional[str] = None,
    port: Optional[int] = None,
) -> Dict[str, Any]:
    resolved_version = version or get_system_version()
    _hermes_port = os.environ.get("MIOS_PORTS_HERMES", "8720")
    resolved_backend = backend or os.environ.get(
        "MIOS_AGENT_PIPE_BACKEND", f"http://localhost:{_hermes_port}")
    resolved_port = port or int(os.environ.get("MIOS_PORTS_AGENT_PIPE", "8700"))
    return {
        "status": status,
        "version": resolved_version,
        "backend": resolved_backend,
        "port": resolved_port,
    }
