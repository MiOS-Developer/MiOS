#!/usr/bin/env python3
"""
adapters.py - Universal Cross-Harness CLI Adapter Registry.
Translates canonical Dev Loop operational intents into harness-specific CLI invocations.
Supported harnesses: claude, openai, codex, gemini, cloudcode, copilot, antigravity, opencode, cursor, custom.
"""

import abc
import os
import shlex
from dataclasses import dataclass, field
from typing import Dict, List, Optional

class BaseHarnessAdapter(abc.ABC):
    @property
    @abc.abstractmethod
    def name(self) -> str:
        pass

    @abc.abstractmethod
    def format_task_command(self, worktree: str, command: str) -> str:
        pass

    def get_doctor_command(self) -> str:
        return "/doctor"

    def get_plan_command(self, task: str) -> str:
        return f"/plan {shlex.quote(task)}"

    def get_compact_command(self, focus: Optional[str] = None) -> str:
        return f"/compact {shlex.quote(focus)}" if focus else "/compact"

    def build_exec_string(self, worktree: str, command: str, test_cmd: Optional[str] = None) -> str:
        base_cmd = self.format_task_command(worktree, command)
        if test_cmd:
            return (
                f"{base_cmd} && {{ "
                f"echo -e '\033[0;32m==> DevLoop: Execution succeeded. Running test verification...\033[0m'; "
                f"cd {shlex.quote(worktree)} && {test_cmd}; }}"
            )
        return base_cmd

class ClaudeAdapter(BaseHarnessAdapter):
    @property
    def name(self) -> str:
        return "claude"

    def format_task_command(self, worktree: str, command: str) -> str:
        return f"claude -w {shlex.quote(worktree)} -p {shlex.quote(command)}"

class OpenAIAdapter(BaseHarnessAdapter):
    @property
    def name(self) -> str:
        return "openai"

    def format_task_command(self, worktree: str, command: str) -> str:
        return f"openai-agent-cli -w {shlex.quote(worktree)} -p {shlex.quote(command)}"

    def get_doctor_command(self) -> str:
        return "/skill:doctor"

    def get_plan_command(self, task: str) -> str:
        return f"/skill:plan {shlex.quote(task)}"

class CodexAdapter(BaseHarnessAdapter):
    @property
    def name(self) -> str:
        return "codex"

    def format_task_command(self, worktree: str, command: str) -> str:
        return f"codex exec -C {shlex.quote(worktree)} --prompt {shlex.quote(command)}"

class CloudCodeAdapter(BaseHarnessAdapter):
    @property
    def name(self) -> str:
        return "cloudcode"

    def format_task_command(self, worktree: str, command: str) -> str:
        return f"cloudcode cli -w {shlex.quote(worktree)} --exec {shlex.quote(command)}"

    def get_plan_command(self, task: str) -> str:
        return f"/workflows:plan {shlex.quote(task)}"

class GeminiAdapter(BaseHarnessAdapter):
    @property
    def name(self) -> str:
        return "gemini"

    def format_task_command(self, worktree: str, command: str) -> str:
        return f"gemini code -w {shlex.quote(worktree)} -p {shlex.quote(command)}"

    def get_plan_command(self, task: str) -> str:
        return f"/workflows:plan {shlex.quote(task)}"

class CopilotAdapter(BaseHarnessAdapter):
    @property
    def name(self) -> str:
        return "copilot"

    def format_task_command(self, worktree: str, command: str) -> str:
        return f"gh copilot run -w {shlex.quote(worktree)} -p {shlex.quote(command)}"

class AntigravityAdapter(BaseHarnessAdapter):
    @property
    def name(self) -> str:
        return "antigravity"

    def format_task_command(self, worktree: str, command: str) -> str:
        return f"antigravity run -w {shlex.quote(worktree)} --task {shlex.quote(command)}"

    def get_plan_command(self, task: str) -> str:
        return f"/workflows:plan {shlex.quote(task)}"

class OpenCodeAdapter(BaseHarnessAdapter):
    @property
    def name(self) -> str:
        return "opencode"

    def format_task_command(self, worktree: str, command: str) -> str:
        return f"opencode run -d {shlex.quote(worktree)} -p {shlex.quote(command)}"

class CursorAdapter(BaseHarnessAdapter):
    @property
    def name(self) -> str:
        return "cursor"

    def format_task_command(self, worktree: str, command: str) -> str:
        return f"cursor-cli -w {shlex.quote(worktree)} -p {shlex.quote(command)}"

class CustomAdapter(BaseHarnessAdapter):
    @property
    def name(self) -> str:
        return "custom"

    def format_task_command(self, worktree: str, command: str) -> str:
        return f"cd {shlex.quote(worktree)} && {command}"

class GeminiSparkAdapter(BaseHarnessAdapter):
    @property
    def name(self) -> str:
        return "spark"

    def format_task_command(self, worktree: str, command: str) -> str:
        return f"cd {shlex.quote(worktree)} && {command}"

    def get_doctor_command(self) -> str:
        return "/doctor"

    def get_plan_command(self, task: str) -> str:
        return f"/plan {shlex.quote(task)}"

    def get_compact_command(self, focus: Optional[str] = None) -> str:
        return f"/compact {shlex.quote(focus)}" if focus else "/compact"

ADAPTER_REGISTRY: Dict[str, BaseHarnessAdapter] = {
    "claude": ClaudeAdapter(),
    "openai": OpenAIAdapter(),
    "codex": CodexAdapter(),
    "cloudcode": CloudCodeAdapter(),
    "gemini": GeminiAdapter(),
    "copilot": CopilotAdapter(),
    "antigravity": AntigravityAdapter(),
    "opencode": OpenCodeAdapter(),
    "cursor": CursorAdapter(),
    "spark": GeminiSparkAdapter(),
    "gemini-spark": GeminiSparkAdapter(),
    "custom": CustomAdapter()
}

def get_adapter(harness_name: str) -> BaseHarnessAdapter:
    canonical = harness_name.strip().lower()
    if canonical in ADAPTER_REGISTRY:
        return ADAPTER_REGISTRY[canonical]
    raise ValueError(f"Unsupported harness: {harness_name}")

def list_supported_harnesses() -> List[str]:
    return list(ADAPTER_REGISTRY.keys())

def build_command(harness: str, command: str, worktree: str, test_cmd: Optional[str] = None) -> str:
    return get_adapter(harness).build_exec_string(worktree, command, test_cmd)
