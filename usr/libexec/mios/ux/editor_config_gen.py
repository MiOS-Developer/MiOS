#!/usr/bin/env python3
# AI-hint: VS Code, Cursor, and Continue IDE configuration generator routing completions to local MiOS brain.
# AI-related: tests/test-ux.py, usr/share/mios/mios.toml
# AI-functions: EditorConfigGen, main
"""
MiOS Editor AI Configuration Projector & Generator (T-460).

Automatically projects IDE configuration settings for VS Code, Cursor, and Continue:
- Routes chat/orchestration requests to http://localhost:8700/v1 (agent-pipe / Hermes).
- Routes code completion and fast tab autocomplete to http://localhost:8500/v1 (mios-llm-light).
- Preconfigures nomic-embed-text for local embeddings without cloud dependencies.
- Guarantees 100% offline pair programming per Architectural Law 5 (UNIFIED-AI-REDIRECTS).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

_HERE = os.path.dirname(os.path.abspath(__file__))
_LIB_PATH = os.path.normpath(os.path.join(_HERE, "..", "..", "..", "lib", "mios"))
if os.path.isdir(_LIB_PATH) and _LIB_PATH not in sys.path:
    sys.path.insert(0, _LIB_PATH)

try:
    import mios_toml
except ImportError:
    mios_toml = None

DEFAULT_AGENT_ENDPOINT = "http://localhost:8700/v1"
DEFAULT_INFERENCE_ENDPOINT = "http://localhost:8500/v1"
DEFAULT_MODEL = "mios-opencode"
DEFAULT_EMBED_MODEL = "nomic-embed-text"
DEFAULT_API_KEY = "mios-local"

class EditorConfigGen:
    """Projects local AI endpoint configurations into developer editor environments."""

    def __init__(
        self,
        agent_endpoint: str = DEFAULT_AGENT_ENDPOINT,
        inference_endpoint: str = DEFAULT_INFERENCE_ENDPOINT,
        default_model: str = DEFAULT_MODEL,
        embed_model: str = DEFAULT_EMBED_MODEL,
        api_key: str = DEFAULT_API_KEY,
        mock: bool = False,
        dry_run: bool = False,
        verbose: bool = False,
    ) -> None:
        self.agent_endpoint = self._resolve_endpoint("ai.endpoint", agent_endpoint)
        self.inference_endpoint = inference_endpoint
        self.default_model = self._resolve_endpoint("ai.model", default_model)
        self.embed_model = self._resolve_endpoint("ai.embed_model", embed_model)
        self.api_key = api_key
        self.mock = mock
        self.dry_run = dry_run
        self.verbose = verbose

    def _resolve_endpoint(self, key_path: str, default_val: str) -> str:
        """Resolve value from mios_toml SSOT if available."""
        if mios_toml is not None:
            try:
                parts = key_path.split(".", 1)
                sect = parts[0]
                k = parts[1] if len(parts) > 1 else ""
                val = mios_toml.get(sect, k, default=default_val)
                if val:
                    return str(val)
            except Exception:
                pass
        return default_val

    def render_vscode_settings(self) -> Dict[str, Any]:
        """Generate VS Code settings dictionary pre-configured for local MiOS AI."""
        return {
            "github.copilot.advanced": {
                "debug.overrideEngine": self.default_model,
                "debug.overrideCapiEngine": self.default_model,
                "debug.testOverrideProxyUrl": self.agent_endpoint.replace("/v1", ""),
            },
            "openai.apiBase": self.agent_endpoint,
            "openai.apiKey": self.api_key,
            "openai.model": self.default_model,
            "editor.inlineSuggest.enabled": True,
            "editor.tabCompletion": "on",
            "editor.formatOnSave": True,
            "telemetry.telemetryLevel": "off",
        }

    def render_cursor_settings(self) -> Dict[str, Any]:
        """Generate Cursor settings dictionary pre-configured for local MiOS AI."""
        return {
            "cursor.general.openAiBaseUrl": self.agent_endpoint,
            "cursor.general.apiKey": self.api_key,
            "cursor.general.model": self.default_model,
            "cursor.general.customModels": [
                {
                    "name": self.default_model,
                    "endpoint": self.agent_endpoint,
                },
                {
                    "name": "mios-llm-light",
                    "endpoint": self.inference_endpoint,
                },
            ],
            "cursor.cpp.disabled": False,
            "cursor.chat.alwaysUseSearch": False,
        }

    def render_continue_config(self) -> Dict[str, Any]:
        """Generate Continue extension config.json dictionary."""
        return {
            "models": [
                {
                    "title": "MiOS Agent Pipe (Orchestrator)",
                    "provider": "openai",
                    "model": self.default_model,
                    "apiBase": self.agent_endpoint,
                    "apiKey": self.api_key,
                },
                {
                    "title": "MiOS LLM Light (Direct Fast Inference)",
                    "provider": "openai",
                    "model": self.default_model,
                    "apiBase": self.inference_endpoint,
                    "apiKey": self.api_key,
                },
            ],
            "tabAutocompleteModel": {
                "title": "MiOS Autocomplete",
                "provider": "openai",
                "model": self.default_model,
                "apiBase": self.inference_endpoint,
                "apiKey": self.api_key,
            },
            "embeddingsProvider": {
                "provider": "openai",
                "model": self.embed_model,
                "apiBase": self.inference_endpoint,
                "apiKey": self.api_key,
            },
            "customCommands": [
                {
                    "name": "refactor",
                    "prompt": "Refactor the selected code adhering to MiOS architectural patterns and clean Python conventions.",
                    "description": "Refactor selected code with MiOS standards",
                },
                {
                    "name": "test",
                    "prompt": "Write deterministic unit tests with mocks for the selected code.",
                    "description": "Generate unit test suite",
                },
            ],
            "allowAnonymousTelemetry": False,
            "docs": [],
        }

    def generate(
        self,
        target: str = "all",
        out_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generate configurations for specified editor targets."""
        target_norm = target.lower()
        results: Dict[str, Any] = {}
        files_written: List[str] = []

        targets_to_run = []
        if target_norm in ("vscode", "all"):
            targets_to_run.append("vscode")
        if target_norm in ("cursor", "all"):
            targets_to_run.append("cursor")
        if target_norm in ("continue", "all"):
            targets_to_run.append("continue")

        if not targets_to_run:
            raise ValueError(f"Unknown editor target: {target}. Valid: vscode, cursor, continue, all")

        for tgt in targets_to_run:
            if tgt == "vscode":
                cfg = self.render_vscode_settings()
                filename = "vscode-settings.json"
            elif tgt == "cursor":
                cfg = self.render_cursor_settings()
                filename = "cursor-settings.json"
            elif tgt == "continue":
                cfg = self.render_continue_config()
                filename = "continue-config.json"
            else:
                continue

            results[tgt] = cfg
            if out_dir:
                file_path = os.path.join(out_dir, filename)
            else:
                file_path = filename

            if not self.mock and not self.dry_run and out_dir:
                os.makedirs(out_dir, exist_ok=True)
                with open(file_path, "w", encoding="utf-8") as f:
                    json.dump(cfg, f, indent=2)
            files_written.append(file_path)

        return {
            "status": "success",
            "action": "generate",
            "targets": targets_to_run,
            "endpoints": {
                "agent_endpoint": self.agent_endpoint,
                "inference_endpoint": self.inference_endpoint,
                "model": self.default_model,
                "embed_model": self.embed_model,
            },
            "files": files_written,
            "configurations": results,
            "dry_run": self.dry_run,
            "mock": self.mock,
        }

    def check(self, target_path: str) -> Dict[str, Any]:
        """Check an existing editor configuration file for local endpoint compliance."""
        if self.mock:
            return {
                "status": "compliant",
                "path": target_path,
                "local_endpoint": True,
                "cloud_keys_detected": False,
                "mock": True,
            }

        if not os.path.exists(target_path):
            return {
                "status": "missing",
                "path": target_path,
                "error": "Configuration file not found",
            }

        try:
            with open(target_path, "r", encoding="utf-8") as f:
                content = json.load(f)

            def walk(value, path=""):
                if isinstance(value, dict):
                    for key, item in value.items():
                        field = f"{path}.{key}" if path else key
                        yield field, key.rsplit(".", 1)[-1].replace("_", "").lower(), item
                        yield from walk(item, field)
                elif isinstance(value, list):
                    for index, item in enumerate(value):
                        yield from walk(item, f"{path}[{index}]")

            def endpoint(value):
                if not isinstance(value, str) or value != value.strip():
                    return None
                try:
                    url = urlsplit(value)
                    if (url.scheme not in ("http", "https")
                            or url.hostname not in ("localhost", "127.0.0.1", "::1")
                            or url.username is not None or url.password is not None
                            or url.query or url.fragment):
                        return None
                    return (url.scheme, url.hostname, url.port, url.path.rstrip("/"))
                except ValueError:
                    return None

            allowed = {endpoint(self.agent_endpoint), endpoint(self.inference_endpoint)} - {None}
            proxy_allowed = {endpoint(self.agent_endpoint.removesuffix("/v1"))} - {None}
            routes, invalid, cloud_keys = [], [], set()
            for field, key, value in walk(content):
                if key in ("apibase", "openaibaseurl", "endpoint", "baseurl", "testoverrideproxyurl"):
                    routes.append(field)
                    approved = proxy_allowed if key == "testoverrideproxyurl" else allowed
                    if endpoint(value) not in approved:
                        invalid.append(field)
                if key in ("apikey", "token") and isinstance(value, str):
                    for prefix in ("sk-", "ghp_"):
                        if value.startswith(prefix) and not value.startswith("sk-local"):
                            cloud_keys.add(prefix)

            has_local = bool(routes) and not invalid
            is_compliant = has_local and not cloud_keys

            return {
                "status": "compliant" if is_compliant else "non-compliant",
                "path": target_path,
                "local_endpoint": has_local,
                "cloud_keys_detected": len(cloud_keys) > 0,
                "detected_cloud_tokens": sorted(cloud_keys),
                "invalid_endpoint_fields": invalid,
            }
        except Exception as e:
            return {
                "status": "error",
                "path": target_path,
                "error": str(e),
            }

def main() -> int:
    parser = argparse.ArgumentParser(
        description="MiOS VS Code, Cursor & Continue Editor AI Config Projector (T-460)"
    )
    parser.add_argument(
        "--target",
        choices=["vscode", "cursor", "continue", "all"],
        default="all",
        help="Target editor environment",
    )
    parser.add_argument("--generate", action="store_true", help="Generate configuration files")
    parser.add_argument("--out-dir", help="Output directory path for generated configuration files")
    parser.add_argument("--check", help="Check compliance of an existing configuration file")
    parser.add_argument("--agent-endpoint", default=DEFAULT_AGENT_ENDPOINT, help="Agent orchestration endpoint URL")
    parser.add_argument("--inference-endpoint", default=DEFAULT_INFERENCE_ENDPOINT, help="Inference lane endpoint URL")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Default coding model name")
    parser.add_argument("--mock", action="store_true", help="Deterministic in-memory mock mode")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without writing files")
    parser.add_argument("--json", action="store_true", help="Emit output as JSON")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose output")

    args = parser.parse_args()

    projector = EditorConfigGen(
        agent_endpoint=args.agent_endpoint,
        inference_endpoint=args.inference_endpoint,
        default_model=args.model,
        mock=args.mock,
        dry_run=args.dry_run,
        verbose=args.verbose,
    )

    try:
        if args.check:
            result = projector.check(args.check)
        else:
            result = projector.generate(target=args.target, out_dir=args.out_dir)

        if args.json:
            print(json.dumps(result, indent=2))
        else:
            status = result.get("status", "ok")
            print(f"[editor_config_gen] Status: {status}")
            if "files" in result:
                for f in result["files"]:
                    print(f"  Generated: {f}")
        return 0 if result.get("status") in ("success", "compliant") else 1
    except Exception as e:
        err = {"status": "error", "error": str(e)}
        if args.json:
            print(json.dumps(err, indent=2))
        else:
            print(f"[editor_config_gen] ERROR: {e}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())
