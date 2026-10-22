#!/usr/bin/env python3
# AI-hint: Declarative Traefik / Envoy service mesh proxy generator and Unix socket router for MiOS.
import argparse
import json
import os
import sys
from typing import Dict, List, Optional, Any

DEFAULT_SOCKET_DIR = "/run/mios/sockets"
DEFAULT_ROUTES = [
    {
        "name": "agent_pipe",
        "listen_port": 8700,
        "socket_path": "/run/mios/sockets/agent_pipe.sock",
        "enable_mtls": False,
        "trace_propagation": True,
    },
    {
        "name": "hermes_gateway",
        "listen_port": 8720,
        "socket_path": "/run/mios/sockets/hermes.sock",
        "enable_mtls": False,
        "trace_propagation": True,
    },
    {
        "name": "llm_light",
        "listen_port": 8500,
        "socket_path": "/run/mios/sockets/llm_light.sock",
        "enable_mtls": False,
        "trace_propagation": False,
    },
    {
        "name": "cluster_federation",
        "listen_port": 8443,
        "socket_path": "/run/mios/sockets/federation.sock",
        "enable_mtls": True,
        "trace_propagation": True,
    },
]

class ServiceMeshGenerator:
    """Synthesizes dynamic Traefik / Envoy proxy configurations with Unix domain socket routing and W3C tracing."""

    def __init__(
        self,
        routes: Optional[List[Dict[str, Any]]] = None,
        socket_dir: str = DEFAULT_SOCKET_DIR,
        dry_run: bool = False,
    ):
        self.routes = routes if routes is not None else DEFAULT_ROUTES
        self.socket_dir = socket_dir
        self.dry_run = dry_run

    def render_traefik_dynamic_config(self) -> Dict[str, Any]:
        """Renders Traefik dynamic YAML/JSON routing structure."""
        http_routers = {}
        http_services = {}

        for r in self.routes:
            name = r["name"]
            port = r["listen_port"]
            sock = r.get("socket_path", f"{self.socket_dir}/{name}.sock")
            mtls = r.get("enable_mtls", False)
            trace = r.get("trace_propagation", True)

            router_key = f"{name}-router"
            service_key = f"{name}-service"

            router_config = {
                "entryPoints": [f"web-{port}"],
                "service": service_key,
                "rule": "PathPrefix(`/`)",
            }
            if mtls:
                router_config["tls"] = {
                    "options": "strict-mtls@file",
                }

            if trace:
                router_config["middlewares"] = ["traceparent-injector@file"]

            http_routers[router_key] = router_config
            http_services[service_key] = {
                "loadBalancer": {
                    "servers": [
                        {"url": f"http://unix:{sock}"}
                    ]
                }
            }

        return {
            "http": {
                "routers": http_routers,
                "services": http_services,
                "middlewares": {
                    "traceparent-injector": {
                        "headers": {
                            "customRequestHeaders": {
                                "X-Forwarded-Proto": "http",
                            }
                        }
                    }
                },
            }
        }

    def write_config(self, output_path: str) -> Dict[str, Any]:
        """Writes rendered Traefik service mesh configuration."""
        config = self.render_traefik_dynamic_config()
        if self.dry_run:
            return {
                "status": "dry_run",
                "output_path": output_path,
                "routes_count": len(self.routes),
                "config": config,
                "mock": True,
            }

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)

        return {
            "status": "success",
            "output_path": output_path,
            "routes_count": len(self.routes),
            "mock": False,
        }

def main():
    parser = argparse.ArgumentParser(description="MiOS Declarative Service Mesh Proxy Generator")
    parser.add_argument("--output", default="/etc/mios/traefik/dynamic.json", help="Dynamic config destination")
    parser.add_argument("--dry-run", action="store_true", help="Simulate config generation")
    args = parser.parse_args()

    mesh = ServiceMeshGenerator(dry_run=args.dry_run)
    res = mesh.write_config(args.output)
    print(json.dumps(res, indent=2))

if __name__ == "__main__":
    main()
