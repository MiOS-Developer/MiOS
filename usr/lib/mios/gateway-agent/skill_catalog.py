# AI-hint: MiOS system and orchestration module providing skill catalog capabilities.
# AI-related: mios-skill, [ports].agent_pipe
# AI-functions: __init__, start, stop, reload_catalog, get_tools, _create_tool_instance, forward, SkillCatalogLoader, DynamicSkillTool

import asyncio
import json
import logging
import os
import time
import httpx
from typing import List
from smolagents import Tool

log = logging.getLogger("gateway-agent-skills")

class SkillCatalogLoader:
    def __init__(self, catalog_path: str = "/var/lib/mios/skills/catalog.json", skill_refresh_seconds: int = 300):
        self.catalog_path = catalog_path
        self.skill_refresh_seconds = skill_refresh_seconds
        self.cached_skills = []
        self._refresh_task = None
        self._lock = asyncio.Lock()

    def start(self) -> None:
        self.reload_catalog()
        self._refresh_task = asyncio.create_task(self._refresh_loop())

    def stop(self) -> None:
        if self._refresh_task:
            self._refresh_task.cancel()
            self._refresh_task = None

    def reload_catalog(self) -> None:
        if not os.path.exists(self.catalog_path):
            log.warning("Skill catalog file not found at %s. Stabbing empty list.", self.catalog_path)
            self.cached_skills = []
            return
        try:
            with open(self.catalog_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.cached_skills = data.get("tools") or []
                log.info("Loaded %d skills from catalog at %s", len(self.cached_skills), self.catalog_path)
        except Exception as e:
            log.error("Failed to load skill catalog from %s: %s", self.catalog_path, e)

    async def _refresh_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self.skill_refresh_seconds)
                async with self._lock:
                    self.reload_catalog()
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.warning("Skill catalog refresh failed: %s", e)

    def get_tools(self) -> List[Tool]:
        tools = []
        for skill_def in self.cached_skills:
            try:
                tool_inst = self._create_tool_instance(skill_def)
                tools.append(tool_inst)
            except Exception as e:
                log.warning("Failed to map skill to smolagents.Tool: %s", e)
        return tools

    def _create_tool_instance(self, skill_def: dict) -> Tool:
        func = skill_def.get("function", {}) or {}
        name = func.get("name", "")
        description = func.get("description", "")
        parameters = func.get("parameters", {}) or {}

        properties = parameters.get("properties", {}) or {}
        required = parameters.get("required", []) or []
        original_skill_name = skill_def.get("x-mios-skill", name)

        inputs = {}
        for param_name, param_schema in properties.items():
            inputs[param_name] = {
                "type": param_schema.get("type", "string"),
                "description": param_schema.get("description", f"Parameter {param_name}")
            }
            if param_name not in required:
                inputs[param_name]["nullable"] = True

        class DynamicSkillTool(Tool):
            def __init__(self, name_val, desc_val, inputs_val, orig_name_val):
                self.name = name_val
                self.description = desc_val
                self.inputs = inputs_val
                self.original_skill_name = orig_name_val
                self.output_type = "string"
                self.skip_forward_signature_validation = True
                super().__init__()

            def forward(self, **kwargs) -> str:
                # Law 5: the legacy orchestrator port is RETIRED -- resolve the front door the way
                # [ai].endpoint spells it, "http://localhost:${MIOS_PORTS_AGENT_PIPE}/v1".
                _pipe_port = os.environ.get("MIOS_PORTS_AGENT_PIPE", "8700")
                ai_endpoint = os.environ.get(
                    "MIOS_AI_ENDPOINT", "http://localhost:%s/v1" % _pipe_port)
                orchestrator_root = ai_endpoint.replace("/v1", "").rstrip("/")
                url = f"{orchestrator_root}/skills/run"

                payload = {
                    "name": self.original_skill_name,
                    "params": kwargs
                }
                log.info("Executing skill '%s' via orchestrator endpoint: %s", self.original_skill_name, url)
                try:
                    with httpx.Client(timeout=30.0) as client:
                        resp = client.post(url, json=payload)
                        if resp.status_code != 200:
                            return f"Error executing skill {self.name}: HTTP {resp.status_code} - {resp.text[:200]}"

                        body = resp.json()
                        if body.get("success"):
                            steps = body.get("steps") or []
                            out = f"Skill '{self.original_skill_name}' executed successfully.\n"
                            for idx, step in enumerate(steps):
                                verb = step.get("verb", "unknown")
                                res = step.get("result", "")
                                out += f"Step {idx + 1} ({verb}): {res}\n"
                            return out
                        else:
                            failures = body.get("failures") or []
                            error = body.get("error") or "Unknown error"
                            return f"Skill '{self.original_skill_name}' failed: {error}. Failures: {', '.join(failures)}"
                except Exception as e:
                    return f"Error connecting to skill execution server: {e}"

        return DynamicSkillTool(name, description, inputs, original_skill_name)
