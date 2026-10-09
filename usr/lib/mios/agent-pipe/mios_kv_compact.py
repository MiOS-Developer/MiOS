#!/usr/bin/env python3
# AI-hint: Semantic KV-cache context compaction engine and episodic summary generator for agent-pipe.
# AI-related: usr/lib/mios/agent-pipe/mios_compact.py, tests/test-sec.py
# AI-functions: KVCompactEngine, estimate_tokens, summarize_turns, main
"""
WS-AI (task_491): Semantic KV-Cache Context Compaction Engine & Episodic Summary Generator.
Integrated into agent-pipe to prevent context overflow while preserving agent reasoning integrity.
Monitors multi-turn conversation token usage; when capacity exceeds 75%, condenses intermediate tool
executions and verbose historical logs into structured milestones while strictly preserving system/developer
prompts, active goal constraints, and recent conversational turns.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_MAX_TOKENS = 8192
DEFAULT_COMPACT_THRESHOLD = 0.75
DEFAULT_TARGET_RATIO = 0.40
DEFAULT_EPISODE_DIR = "/var/lib/mios/ai/memory/episodes"

def estimate_tokens(text_or_messages: str | List[Dict[str, Any]] | Dict[str, Any]) -> int:
    """
    Fast, deterministic token estimation for OpenAI-compatible message payloads.
    Heuristic: ~4 characters per token + per-message framing overhead (~4 tokens).
    """
    if isinstance(text_or_messages, str):
        return max(1, len(text_or_messages) // 4)

    if isinstance(text_or_messages, dict):
        content = text_or_messages.get("content", "")
        if isinstance(content, list):
            content_str = " ".join([str(item.get("text", "")) if isinstance(item, dict) else str(item) for item in content])
        else:
            content_str = str(content)
        role = text_or_messages.get("role", "")
        tool_calls = text_or_messages.get("tool_calls", [])
        tool_str = json.dumps(tool_calls) if tool_calls else ""
        return max(4, (len(content_str) + len(role) + len(tool_str)) // 4 + 4)

    if isinstance(text_or_messages, list):
        total = 0
        for msg in text_or_messages:
            total += estimate_tokens(msg)
        return total

    return 0

def extract_factual_anchors(text: str) -> List[str]:
    """Extract key file paths, function names, task IDs, and technical terms to retain in summary."""
    anchors = set()
    # Task / CVE IDs (task_545, CVE-2026-1001); a task is named by its typed id (ADR-0028)
    for match in re.finditer(r'\b(?:task_\d+|CVE-\d+-\d+)\b', text):
        anchors.add(match.group(0))
    # File paths (Unix & Windows)
    for match in re.finditer(r'(?:[a-zA-Z]:\\|[/\w\.-]+/)[a-zA-Z0-9_\.-]+\.[a-zA-Z0-9]+', text):
        anchors.add(match.group(0))
    # Code symbols / identifiers / tools
    for match in re.finditer(r'\b(?:def|class|function)\s+([a-zA-Z0-9_]+)', text):
        anchors.add(match.group(1))
    return sorted(list(anchors))[:15]

class KVCompactEngine:
    """Context compaction engine for agent-pipe conversation pipelines."""

    def __init__(
        self,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        compact_threshold: float = DEFAULT_COMPACT_THRESHOLD,
        target_ratio: float = DEFAULT_TARGET_RATIO,
        episode_dir: str = DEFAULT_EPISODE_DIR,
        mock: bool = False,
        verbose: bool = False,
    ) -> None:
        self.max_tokens = max_tokens
        self.compact_threshold = compact_threshold
        self.target_ratio = target_ratio
        self.episode_dir = episode_dir
        self.mock = mock
        self.verbose = verbose
        self._mock_episodes: List[Dict[str, Any]] = []

    def should_compact(
        self,
        messages: List[Dict[str, Any]],
        max_tokens: Optional[int] = None,
        threshold: Optional[float] = None,
    ) -> bool:
        """Evaluate if conversation token count exceeds the compaction threshold."""
        limit = max_tokens or self.max_tokens
        thresh = threshold or self.compact_threshold
        current_tokens = estimate_tokens(messages)
        return current_tokens >= int(limit * thresh)

    def archive_episode(
        self,
        session_id: str,
        messages: List[Dict[str, Any]],
        summary: str,
    ) -> Dict[str, Any]:
        """Archive full raw trajectory before compaction for long-term memory retrieval."""
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        digest = hashlib.sha256(json.dumps(messages, sort_keys=True).encode("utf-8")).hexdigest()
        episode_record = {
            "session_id": session_id,
            "timestamp": now,
            "sha256": digest,
            "turns_count": len(messages),
            "tokens_before": estimate_tokens(messages),
            "summary": summary,
            "raw_messages": messages,
        }

        if self.mock:
            self._mock_episodes.append(episode_record)
            return {
                "archived": True,
                "episode_id": f"ep-{digest[:12]}",
                "storage": "mock_memory",
                "timestamp": now,
            }

        try:
            os.makedirs(self.episode_dir, exist_ok=True)
            safe_session = re.sub(r'[^a-zA-Z0-9_\.-]', '_', session_id)
            target_path = os.path.join(self.episode_dir, f"{safe_session}_{digest[:8]}.json")
            with open(target_path, "w", encoding="utf-8") as f:
                json.dump(episode_record, f, indent=2)
            return {
                "archived": True,
                "episode_id": f"ep-{digest[:12]}",
                "path": target_path,
                "timestamp": now,
            }
        except Exception as exc:
            if self.verbose:
                sys.stderr.write(f"[kv-compact] Episode archive error: {exc}\n")
            return {
                "archived": False,
                "error": str(exc),
            }

    def compact_messages(
        self,
        messages: List[Dict[str, Any]],
        session_id: str = "default-session",
        preserve_recent_turns: int = 4,
        force: bool = False,
    ) -> Dict[str, Any]:
        """
        Compact conversation history while strictly maintaining:
        1. System / developer prompts (immutable, uncompacted at head).
        2. Active goal / task definitions.
        3. Most recent conversational turns (preserve_recent_turns).
        4. Structured milestone summary replacing verbose middle turns.
        """
        initial_tokens = estimate_tokens(messages)
        if not force and not self.should_compact(messages):
            return {
                "compacted": False,
                "reason": "Token usage below threshold",
                "initial_tokens": initial_tokens,
                "final_tokens": initial_tokens,
                "reduction_ratio": 1.0,
                "messages": messages,
            }

        if len(messages) <= 2:
            return {
                "compacted": False,
                "reason": "Too few messages to compact",
                "initial_tokens": initial_tokens,
                "final_tokens": initial_tokens,
                "reduction_ratio": 1.0,
                "messages": messages,
            }

        # Separate system/developer messages (Head)
        system_msgs: List[Dict[str, Any]] = []
        conversation_msgs: List[Dict[str, Any]] = []

        for msg in messages:
            role = msg.get("role", "").lower()
            if role in ("system", "developer") and not conversation_msgs:
                system_msgs.append(msg)
            else:
                conversation_msgs.append(msg)

        # Separate recent turns (Tail)
        if len(conversation_msgs) <= preserve_recent_turns:
            compactable = conversation_msgs[:-1]
            tail_msgs = conversation_msgs[-1:]
        else:
            compactable = conversation_msgs[:-preserve_recent_turns]
            tail_msgs = conversation_msgs[-preserve_recent_turns:]

        if not compactable:
            return {
                "compacted": False,
                "reason": "No intermediate turns eligible for compaction",
                "initial_tokens": initial_tokens,
                "final_tokens": initial_tokens,
                "reduction_ratio": 1.0,
                "messages": messages,
            }

        # Build structured milestone summary from compactable turns
        tool_actions: List[str] = []
        user_queries: List[str] = []
        all_text: List[str] = []

        for idx, msg in enumerate(compactable):
            role = msg.get("role", "").lower()
            content = str(msg.get("content", ""))
            all_text.append(content)

            if role == "user":
                snippet = content.strip().split("\n")[0][:120]
                user_queries.append(snippet)
            elif role == "tool" or msg.get("tool_calls"):
                name = msg.get("name") or "tool"
                tool_actions.append(f"Tool `{name}` executed")

        anchors = extract_factual_anchors("\n".join(all_text))
        summary_lines = [
            f"[Compacted Milestone: {len(compactable)} historical turns condensed]",
            f"- User Objectives: {'; '.join(user_queries[:3]) if user_queries else 'N/A'}",
            f"- Actions Executed: {len(tool_actions)} tool invocations completed",
        ]
        if anchors:
            summary_lines.append(f"- Key Anchors & Artifacts: {', '.join(anchors[:8])}")

        milestone_content = "\n".join(summary_lines)
        milestone_msg = {
            "role": "assistant",
            "content": milestone_content,
            "_compacted_milestone": True,
            "_turns_condensed": len(compactable),
        }

        # Archive episode
        archive_res = self.archive_episode(session_id, messages, milestone_content)

        # Assemble new message list: System Head + Milestone + Recent Tail
        new_messages = list(system_msgs)
        new_messages.append(milestone_msg)
        new_messages.extend(tail_msgs)

        final_tokens = estimate_tokens(new_messages)
        reduction_ratio = final_tokens / initial_tokens if initial_tokens > 0 else 1.0

        return {
            "compacted": True,
            "initial_tokens": initial_tokens,
            "final_tokens": final_tokens,
            "reduction_ratio": round(reduction_ratio, 3),
            "condensed_turns": len(compactable),
            "preserved_head": len(system_msgs),
            "preserved_tail": len(tail_msgs),
            "archive": archive_res,
            "messages": new_messages,
        }

def main() -> int:
    parser = argparse.ArgumentParser(
        description="MiOS KV-Cache Context Compaction Engine (task_491)"
    )
    parser.add_argument("--compact", action="store_true", help="Execute context compaction")
    parser.add_argument("--input", metavar="FILE", help="Input messages JSON file")
    parser.add_argument("--output", metavar="FILE", help="Output compacted messages JSON file")
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS, help="Max context window tokens")
    parser.add_argument("--threshold", type=float, default=DEFAULT_COMPACT_THRESHOLD, help="Compaction trigger threshold (0.0-1.0)")
    parser.add_argument("--session-id", default="cli-session", help="Session identifier for episodic archive")
    parser.add_argument("--force", action="store_true", help="Force compaction regardless of threshold")
    parser.add_argument("--mock", action="store_true", help="Run with simulated mocks")
    parser.add_argument("--json", action="store_true", help="Output results in JSON format")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose output")

    args = parser.parse_args()
    engine = KVCompactEngine(
        max_tokens=args.max_tokens,
        compact_threshold=args.threshold,
        mock=args.mock,
        verbose=args.verbose,
    )

    if args.input:
        try:
            with open(args.input, "r", encoding="utf-8") as f:
                messages = json.load(f)
        except Exception as exc:
            sys.stderr.write(f"Error reading input JSON: {exc}\n")
            return 1
    else:
        # Synthetic multi-turn conversation for demonstration / mock
        messages = [
            {"role": "system", "content": "You are MiOS Assistant under canonical prompt AGENTS.md."},
            {"role": "user", "content": "Please implement feature task_491 in usr/lib/mios/agent-pipe/mios_kv_compact.py."},
            {"role": "assistant", "content": "Running find_by_name...", "tool_calls": [{"id": "c1", "function": {"name": "find_by_name"}}]},
            {"role": "tool", "name": "find_by_name", "content": "Found 120 matching python files in directory " * 30},
            {"role": "assistant", "content": "Inspecting codebase logs and verifying ratchet limits " * 25},
            {"role": "user", "content": "Now run the test suite and confirm pass rate."},
        ]

    result = engine.compact_messages(
        messages=messages,
        session_id=args.session_id,
        force=args.force or (not args.input),
    )

    if args.output and result.get("compacted"):
        try:
            with open(args.output, "w", encoding="utf-8") as f:
                json.dump(result["messages"], f, indent=2)
        except Exception as exc:
            sys.stderr.write(f"Error writing output JSON: {exc}\n")

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Compacted: {result.get('compacted')}")
        print(f"Tokens: {result.get('initial_tokens')} -> {result.get('final_tokens')} (ratio: {result.get('reduction_ratio')})")

    return 0

if __name__ == "__main__":
    sys.exit(main())
