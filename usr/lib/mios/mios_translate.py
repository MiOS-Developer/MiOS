# AI-hint: Pure Python translation engine for loop.v1 events, Responses items, and cross-harness frame normalization.
# AI-related: usr/libexec/mios/mios-mcp-server, usr/share/mios/mios.toml [mcp], usr/lib/mios/mios_translate.py
"""Pure Python translation engine for cross-harness frames, loop.v1 events and Responses items.

Normalizes frames from AGY, Claude, OpenAI Responses/Codex, and OpenAI Chat Completions
into ordered loop.v1 events and OpenAI Responses items. Enforces credential refusal,
gate evidence validation, and terminal envelope invariants.
"""
from __future__ import annotations

import datetime
import json
import re
import uuid
from typing import Any, Dict, List, Optional

CREDENTIAL_KEYS = {
    "api_key",
    "access_token",
    "refresh_token",
    "authorization",
    "client_secret",
    "password",
    "private_key",
}

# ADR-0029 Standards Mapping Constants
# A2A v1.0.1 ProtoJSON Task States
A2A_TASK_STATE_SUBMITTED = "TASK_STATE_SUBMITTED"
A2A_TASK_STATE_WORKING = "TASK_STATE_WORKING"
A2A_TASK_STATE_INPUT_REQUIRED = "TASK_STATE_INPUT_REQUIRED"
A2A_TASK_STATE_COMPLETED = "TASK_STATE_COMPLETED"
A2A_TASK_STATE_FAILED = "TASK_STATE_FAILED"
A2A_TASK_STATE_REJECTED = "TASK_STATE_REJECTED"
A2A_TASK_STATE_CANCELED = "TASK_STATE_CANCELED"

A2A_TASK_STATES = {
    A2A_TASK_STATE_SUBMITTED,
    A2A_TASK_STATE_WORKING,
    A2A_TASK_STATE_INPUT_REQUIRED,
    A2A_TASK_STATE_COMPLETED,
    A2A_TASK_STATE_FAILED,
    A2A_TASK_STATE_REJECTED,
    A2A_TASK_STATE_CANCELED,
}

# MCP Tasks Extension (2026-07-28)
MCP_TASK_STATUS_WORKING = "working"
MCP_TASK_STATUS_INPUT_REQUIRED = "input_required"
MCP_TASK_STATUS_COMPLETED = "completed"
MCP_TASK_STATUS_FAILED = "failed"
MCP_TASK_STATUS_CANCELLED = "cancelled"

MCP_TASK_STATUSES = {
    MCP_TASK_STATUS_WORKING,
    MCP_TASK_STATUS_INPUT_REQUIRED,
    MCP_TASK_STATUS_COMPLETED,
    MCP_TASK_STATUS_FAILED,
    MCP_TASK_STATUS_CANCELLED,
}

# OpenAI Responses Statuses
OPENAI_STATUS_QUEUED = "queued"
OPENAI_STATUS_IN_PROGRESS = "in_progress"
OPENAI_STATUS_INCOMPLETE = "incomplete"
OPENAI_STATUS_COMPLETED = "completed"
OPENAI_STATUS_FAILED = "failed"
OPENAI_STATUS_CANCELLED = "cancelled"

OPENAI_RESPONSES_STATUSES = {
    OPENAI_STATUS_QUEUED,
    OPENAI_STATUS_IN_PROGRESS,
    OPENAI_STATUS_INCOMPLETE,
    OPENAI_STATUS_COMPLETED,
    OPENAI_STATUS_FAILED,
    OPENAI_STATUS_CANCELLED,
}

# OpenTelemetry GenAI Semantic Conventions
OTEL_GENAI_AGENT_ID = "gen_ai.agent.id"
OTEL_GENAI_AGENT_NAME = "gen_ai.agent.name"
OTEL_GENAI_CONVERSATION_ID = "gen_ai.conversation.id"
OTEL_GENAI_OPERATION_NAME = "gen_ai.operation.name"

OTEL_GENAI_ATTRIBUTES = {
    OTEL_GENAI_AGENT_ID,
    OTEL_GENAI_AGENT_NAME,
    OTEL_GENAI_CONVERSATION_ID,
    OTEL_GENAI_OPERATION_NAME,
}


def enforce_receipt_invariant(receipt_status: str, target_state: str) -> None:
    """Enforce ADR-0029 receipt invariant: a received receipt NEVER maps to WORKING or COMPLETED."""
    norm_status = str(receipt_status or "").strip().lower()
    if norm_status == "received":
        norm_target = str(target_state or "").strip().upper()
        if norm_target in ("WORKING", "COMPLETED", "TASK_STATE_WORKING", "TASK_STATE_COMPLETED", "IN_PROGRESS"):
            raise ValueError(f"RECEIPT INVARIANT VIOLATION: receipt 'received' cannot map to {target_state}")


def map_task_state(source_standard: str, state: str, target_standard: str, is_receipt: bool = False) -> str:
    """Map only declared protocol states; receipts never certify work."""
    src, tgt, value = source_standard.lower(), target_standard.lower(), str(state).strip()
    loop = {"submitted": "submitted", "queued": "submitted", "working": "working",
            "in_progress": "working", "blocked": "input_required", "input_required": "input_required",
            "incomplete": "input_required", "unverified": "input_required", "delivered": "completed",
            "completed": "completed", "errored": "failed", "failed": "failed", "gate_failed": "failed",
            "vacuous": "failed", "timed_out": "failed", "control_invalid": "failed",
            "refused": "rejected", "rejected": "rejected", "cancelled": "cancelled", "canceled": "cancelled"}
    a2a = {"submitted": A2A_TASK_STATE_SUBMITTED, "working": A2A_TASK_STATE_WORKING,
           "input_required": A2A_TASK_STATE_INPUT_REQUIRED, "completed": A2A_TASK_STATE_COMPLETED,
           "failed": A2A_TASK_STATE_FAILED, "rejected": A2A_TASK_STATE_REJECTED, "cancelled": A2A_TASK_STATE_CANCELED}
    mcp = {"working": "working", "input_required": "input_required", "completed": "completed",
           "failed": "failed", "cancelled": "cancelled"}
    responses = {"submitted": "queued", "working": "in_progress", "input_required": "incomplete",
                 "completed": "completed", "failed": "failed", "cancelled": "cancelled"}
    if src in ("receipt", "relay_receipt") or is_receipt or value.lower() == "received":
        if value.lower() not in ("queued", "received"):
            raise ValueError(f"Unknown receipt state: {value}")
        canonical = "submitted"
    elif src == "a2a":
        canonical = {v: k for k, v in a2a.items()}.get(value)
    elif src in ("mcp_tasks", "mcp"):
        canonical = {v: k for k, v in mcp.items()}.get(value.lower())
    elif src in ("responses", "openai", "openai_responses"):
        canonical = {v: k for k, v in responses.items()}.get(value.lower())
    elif src in ("loop.v1", "status"):
        canonical = loop.get(value.lower())
    else:
        raise ValueError(f"Unknown source standard: {src}")
    if canonical is None:
        raise ValueError(f"Unknown {src} task state: {value}")
    if tgt == "a2a":
        return a2a[canonical]
    if tgt in ("mcp_tasks", "mcp"):
        if canonical == "submitted":
            raise ValueError("RECEIPT INVARIANT VIOLATION: MCP tasks has no submitted/queued state")
        return mcp["failed" if canonical == "rejected" else canonical]
    if tgt in ("responses", "openai", "openai_responses"):
        return responses["failed" if canonical == "rejected" else canonical]
    if tgt in ("loop.v1", "status"):
        return {"submitted": "queued", "working": "working", "input_required": "blocked",
                "completed": "delivered", "failed": "errored", "rejected": "refused", "cancelled": "cancelled"}[canonical]
    raise ValueError(f"Unknown target standard: {tgt}")


def build_otel_genai_attributes(
    *,
    agent_id: str = "",
    agent_name: str = "",
    conversation_id: str = "",
    operation_name: str = "invoke_agent",
) -> Dict[str, str]:
    """Build OpenTelemetry GenAI semantic convention attributes."""
    attrs = {
        OTEL_GENAI_OPERATION_NAME: operation_name,
    }
    if agent_id:
        attrs[OTEL_GENAI_AGENT_ID] = str(agent_id)
    if agent_name:
        attrs[OTEL_GENAI_AGENT_NAME] = str(agent_name)
    if conversation_id:
        attrs[OTEL_GENAI_CONVERSATION_ID] = str(conversation_id)
    return attrs


def extract_otel_genai_attributes(frame: Any) -> Dict[str, str]:
    """Extract OpenTelemetry GenAI attributes from a frame, metadata or context."""
    attrs: Dict[str, str] = {}
    if isinstance(frame, list):
        for item in frame:
            found = extract_otel_genai_attributes(item)
            attrs.update(found)
        return attrs
    if not isinstance(frame, dict):
        return attrs

    for k in OTEL_GENAI_ATTRIBUTES:
        if k in frame:
            attrs[k] = str(frame[k])

    md = frame.get("metadata")
    if isinstance(md, dict):
        for k in OTEL_GENAI_ATTRIBUTES:
            if k in md:
                attrs[k] = str(md[k])
        if "agent" in md and OTEL_GENAI_AGENT_NAME not in attrs:
            attrs[OTEL_GENAI_AGENT_NAME] = str(md["agent"])
        if "agent_id" in md and OTEL_GENAI_AGENT_ID not in attrs:
            attrs[OTEL_GENAI_AGENT_ID] = str(md["agent_id"])

    if "contextId" in frame and OTEL_GENAI_CONVERSATION_ID not in attrs:
        attrs[OTEL_GENAI_CONVERSATION_ID] = str(frame["contextId"])
    elif "conversation_id" in frame and OTEL_GENAI_CONVERSATION_ID not in attrs:
        attrs[OTEL_GENAI_CONVERSATION_ID] = str(frame["conversation_id"])

    if "agent" in frame and isinstance(frame["agent"], str) and OTEL_GENAI_AGENT_NAME not in attrs:
        attrs[OTEL_GENAI_AGENT_NAME] = frame["agent"]

    return attrs


ANSI_PATTERN = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|\].*?(?:\x1b\\|\x07))")


def strip_ansi(text: str) -> str:
    """Strip ANSI / VT100 / OSC escape sequences from terminal capture."""
    if not text:
        return ""
    return ANSI_PATTERN.sub("", text)


def extract_embedded_receipt(text: str) -> Optional[Dict[str, Any]]:
    """Extract an embedded JSON receipt (e.g. exitCode, output) from terminal text."""
    if not text:
        return None
    cleaned = strip_ansi(text)
    for match in re.finditer(r"\{[^{}]*\"exitCode\"\s*:\s*\d+[^{}]*\}", cleaned):
        try:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, dict) and "exitCode" in parsed:
                return parsed
        except ValueError:
            continue
    for match in re.finditer(r"(\{.*\})", cleaned, re.DOTALL):
        try:
            parsed = json.loads(match.group(1))
            if isinstance(parsed, dict) and ("exitCode" in parsed or "status" in parsed):
                return parsed
        except ValueError:
            continue
    return None


def credential_key(value: Any) -> Optional[str]:
    """Recursively search for sensitive credential keys in a data structure."""
    if isinstance(value, dict):
        for k, v in value.items():
            norm = re.sub(r"[^a-z0-9]", "", str(k).lower())
            sensitive = {re.sub(r"[^a-z0-9]", "", key.lower()) for key in CREDENTIAL_KEYS}
            if norm in sensitive or norm == "xapikey":
                return k
            found = credential_key(v)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = credential_key(item)
            if found:
                return found
    return None


def detect_source(frame: Dict[str, Any]) -> Optional[str]:
    """Sniff the wire dialect of a single frame."""
    if not isinstance(frame, dict):
        return None
    if "event" in frame:
        return "agy"
    if frame.get("object") == "response" or str(frame.get("type", "")).startswith("response."):
        return "openai"
    obj = frame.get("object")
    if obj in ("chat.completion", "chat.completion.chunk") or "tool_call_id" in frame or "tool_calls" in frame:
        return "openai_chat"
    kind = frame.get("type")
    if kind in ("result", "system", "assistant", "user"):
        if "message" in frame or "result" in frame or "is_error" in frame:
            return "claude"
    if kind in ("function_call", "function_call_output"):
        return "openai"
    if kind == "message":
        content = frame.get("content") or []
        if isinstance(content, list) and any(
            isinstance(b, dict) and b.get("type") in ("output_text", "input_text") for b in content
        ):
            return "openai"
        if isinstance(content, list) and any(
            isinstance(b, dict) and b.get("type") == "text" for b in content
        ):
            return "claude"
    # A2A Message: role in ROLE_AGENT/ROLE_USER, messageId, or parts list
    if "messageId" in frame or frame.get("role") in ("ROLE_AGENT", "ROLE_USER"):
        return "a2a"
    if "parts" in frame and isinstance(frame.get("parts"), list):
        return "a2a"
    # A2A Task
    if "status" in frame and isinstance(frame.get("status"), dict) and "state" in frame["status"]:
        if str(frame["status"]["state"]).startswith("TASK_STATE_"):
            return "a2a"
    if "taskId" in frame and "contextId" in frame and ("status" in frame or "history" in frame or "artifacts" in frame):
        return "a2a"
    # MCP Tasks (2026-07-28)
    if "taskId" in frame and frame.get("status") in MCP_TASK_STATUSES:
        return "mcp_tasks"
    # loop.v1
    if frame.get("schema") == "loop.v1" or "events" in frame:
        return "loop.v1"
    if frame.get("kind") in ("text", "tool_call", "tool_output", "terminal", "handoff"):
        return "loop.v1"
    return None


def _parse_arguments(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            raise ValueError("MALFORMED TOOL ARGUMENTS")
    return value if isinstance(value, dict) else {}


def _required_str(value: Dict[str, Any], key: str, context: str) -> str:
    res = value.get(key)
    if not res or not isinstance(res, str):
        raise ValueError(f"{context}: missing {key}")
    return res


def _agy(frame: Dict[str, Any]) -> List[Dict[str, Any]]:
    if "event" not in frame and "status" in frame:
        return _agy({"event": "result", "result": frame})
    ev = frame.get("event")
    if ev == "init":
        return []
    if ev == "step_update":
        step = frame.get("step_update")
        if not isinstance(step, dict):
            raise ValueError("AGY STEP MISSING")
        st = step.get("step_type")
        if st == "agent_response":
            txt = step.get("text_delta")
            if txt:
                return [{"kind": "text", "role": "assistant", "text": txt}]
            return []
        if st == "tool" and step.get("state") == "DONE":
            info = step.get("tool_info") or {}
            call_id = step.get("call_id")
            if not call_id:
                idx = step.get("step_index", 0)
                conv = frame.get("conversation_id", "session")
                call_id = f"agy:{conv}:{idx}"
            name = step.get("tool_name") or info.get("name")
            if not name:
                raise ValueError("AGY TOOL NAME MISSING")
            args = info.get("parameters") or {}
            res = [{"kind": "tool_call", "call_id": call_id, "name": name, "arguments": args}]
            if "output" in info and info["output"] is not None:
                res.append({"kind": "tool_output", "call_id": call_id, "output": info["output"]})
            return res
        if st in ("tool", "subagent", "user_input", "system_message"):
            return []
        raise ValueError(f"AGY UNKNOWN STEP TYPE: {st}")
    if ev == "result":
        res = frame.get("result")
        if not isinstance(res, dict):
            raise ValueError("AGY RESULT MISSING")
        status = "delivered" if res.get("status") == "SUCCESS" else "errored"
        error = res.get("error")
        text = res.get("response") or ""
        return [{"kind": "terminal", "status": status, "text": text, "error": error}]
    raise ValueError(f"AGY UNKNOWN EVENT: {ev}")


def _claude(frame: Dict[str, Any]) -> List[Dict[str, Any]]:
    if "type" not in frame and "result" in frame:
        wrapped = dict(frame)
        wrapped["type"] = "result"
        return _claude(wrapped)
    kind = frame.get("type")
    if kind == "system":
        return []
    if kind in ("assistant", "user"):
        msg = frame.get("message")
        if not isinstance(msg, dict):
            raise ValueError("CLAUDE MESSAGE MISSING")
        role = msg.get("role") or kind
        blocks = msg.get("content")
        if not isinstance(blocks, list):
            raise ValueError("CLAUDE CONTENT MISSING")
        events = []
        for b in blocks:
            btype = b.get("type")
            if btype == "text":
                txt = b.get("text")
                if txt is None:
                    raise ValueError("CLAUDE TEXT MISSING")
                events.append({"kind": "text", "role": role, "text": txt})
            elif btype == "tool_use":
                cid = b.get("id")
                name = b.get("name")
                if not cid:
                    raise ValueError("CLAUDE TOOL ID MISSING")
                if not name:
                    raise ValueError("CLAUDE TOOL NAME MISSING")
                events.append({"kind": "tool_call", "call_id": cid, "name": name, "arguments": b.get("input") or {}})
            elif btype == "tool_result":
                cid = b.get("tool_use_id")
                if not cid:
                    raise ValueError("CLAUDE TOOL RESULT ID MISSING")
                events.append({"kind": "tool_output", "call_id": cid, "output": b.get("content")})
            else:
                raise ValueError(f"CLAUDE UNKNOWN CONTENT: {btype}")
        return events
    if kind == "result":
        is_err = frame.get("is_error") is True
        text = frame.get("result") or ""
        error = text if is_err else None
        status = "errored" if is_err else "delivered"
        return [{"kind": "terminal", "status": status, "text": text if not is_err else "", "error": error}]
    raise ValueError(f"CLAUDE UNKNOWN EVENT: {kind}")


def _openai_item(item: Dict[str, Any]) -> List[Dict[str, Any]]:
    kind = item.get("type")
    if kind == "message":
        role = item.get("role") or "assistant"
        blocks = item.get("content")
        if not isinstance(blocks, list):
            raise ValueError("RESPONSES MESSAGE CONTENT MISSING")
        events = []
        for b in blocks:
            btype = b.get("type")
            if btype in ("output_text", "input_text"):
                txt = b.get("text")
                if txt is None:
                    raise ValueError("RESPONSES TEXT MISSING")
                events.append({"kind": "text", "role": role, "text": txt})
            else:
                raise ValueError(f"RESPONSES UNKNOWN CONTENT: {btype}")
        return events
    if kind == "function_call":
        cid = item.get("call_id")
        name = item.get("name")
        if not cid:
            raise ValueError("RESPONSES CALL ID MISSING")
        if not name:
            raise ValueError("RESPONSES FUNCTION NAME MISSING")
        args = item.get("arguments")
        if args is None:
            raise ValueError("RESPONSES ARGUMENTS MISSING")
        ev: Dict[str, Any] = {"kind": "tool_call", "call_id": cid, "name": name, "arguments": _parse_arguments(args)}
        if name.startswith("transfer_to_"):
            ev["handoff_agent"] = name[len("transfer_to_"):]
        return [ev]
    if kind == "function_call_output":
        cid = item.get("call_id")
        if not cid:
            raise ValueError("RESPONSES OUTPUT CALL ID MISSING")
        if "output" not in item:
            raise ValueError("RESPONSES OUTPUT MISSING")
        return [{"kind": "tool_output", "call_id": cid, "output": item["output"]}]
    raise ValueError(f"RESPONSES UNKNOWN ITEM: {kind}")


def _openai(frame: Dict[str, Any]) -> List[Dict[str, Any]]:
    if frame.get("object") == "response":
        output = frame.get("output")
        if not isinstance(output, list):
            raise ValueError("RESPONSES OUTPUT ARRAY MISSING")
        events = []
        for it in output:
            events.extend(_openai_item(it))
        has_err = bool(frame.get("error"))
        status = "delivered" if frame.get("status") == "completed" and not has_err else "errored"
        err_msg = frame.get("error", {}).get("message") if isinstance(frame.get("error"), dict) else None
        events.append({"kind": "terminal", "status": status, "text": "", "error": err_msg})
        return events
    kind = frame.get("type")
    if kind == "response.output_item.done":
        it = frame.get("item")
        if not isinstance(it, dict):
            raise ValueError("RESPONSES STREAM ITEM MISSING")
        return _openai_item(it)
    if kind in ("response.completed", "response.failed"):
        resp = frame.get("response") or {}
        has_err = kind == "response.failed" or bool(resp.get("error"))
        status = "errored" if has_err else "delivered"
        err_msg = resp.get("error", {}).get("message") if isinstance(resp.get("error"), dict) else None
        return [{"kind": "terminal", "status": status, "text": "", "error": err_msg}]
    if kind in ("response.output_item.added", "response.output_text.delta"):
        return []
    if kind in ("message", "function_call", "function_call_output"):
        return _openai_item(frame)
    raise ValueError(f"RESPONSES UNKNOWN EVENT: {kind}")


def _chat_message(frame: Dict[str, Any]) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    role = frame.get("role")
    if role == "tool":
        cid = frame.get("tool_call_id") or ""
        events.append({"kind": "tool_output", "call_id": cid, "output": frame.get("content")})
        return events
    content = frame.get("content")
    if content:
        events.append({"kind": "text", "role": role or "assistant", "text": str(content)})
    tool_calls = frame.get("tool_calls")
    if isinstance(tool_calls, list):
        for call in tool_calls:
            fn = call.get("function")
            if not isinstance(fn, dict):
                raise ValueError("CHAT TOOL FUNCTION MISSING")
            cid = call.get("id")
            if not cid:
                raise ValueError("CHAT TOOL ID MISSING")
            name = fn.get("name")
            if not name:
                raise ValueError("CHAT TOOL NAME MISSING")
            if "arguments" not in fn:
                raise ValueError("CHAT ARGUMENTS MISSING")
            args = _parse_arguments(fn["arguments"])
            ev = {"kind": "tool_call", "call_id": cid, "name": name, "arguments": args}
            if name.startswith("transfer_to_"):
                ev["handoff_agent"] = name[len("transfer_to_"):]
            events.append(ev)
    return events


def _openai_chat(frames: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    pending: List[Optional[Dict[str, Any]]] = []

    def flush():
        for slot in pending:
            if slot is not None:
                if not slot.get("id") or not slot.get("name"):
                    raise ValueError("CHAT STREAM TOOL ID OR NAME MISSING")
                full_args = "".join(slot["arguments_fragments"])
                parsed_args = _parse_arguments(full_args if full_args else "{}")
                ev = {
                    "kind": "tool_call",
                    "call_id": slot["id"],
                    "name": slot["name"],
                    "arguments": parsed_args,
                }
                if slot["name"].startswith("transfer_to_"):
                    ev["handoff_agent"] = slot["name"][len("transfer_to_"):]
                events.append(ev)
        pending.clear()

    for frame in frames:
        obj = frame.get("object")
        if obj == "chat.completion":
            flush()
            choices = frame.get("choices") or []
            for choice in choices:
                msg = choice.get("message") or {}
                events.extend(_chat_message(msg))
                fr = choice.get("finish_reason")
                if fr in ("stop", "tool_calls"):
                    events.append({"kind": "terminal", "status": "delivered", "text": "", "error": None})
                elif fr:
                    events.append({"kind": "terminal", "status": "errored", "text": "", "error": f"CHAT FINISH REASON: {fr}"})
        elif obj == "chat.completion.chunk":
            choices = frame.get("choices") or []
            for choice in choices:
                delta = choice.get("delta") or {}
                content = delta.get("content")
                if content:
                    events.append({"kind": "text", "role": delta.get("role") or "assistant", "text": str(content)})
                tool_calls = delta.get("tool_calls")
                if isinstance(tool_calls, list):
                    for call in tool_calls:
                        idx = call.get("index", 0)
                        if idx > 255:
                            raise ValueError("CHAT TOOL INDEX OUT OF RANGE")
                        while len(pending) <= idx:
                            pending.append(None)
                        if pending[idx] is None:
                            pending[idx] = {"id": "", "name": "", "arguments_fragments": []}
                        slot = pending[idx]
                        if call.get("id"):
                            slot["id"] = call["id"]
                        fn = call.get("function") or {}
                        if fn.get("name"):
                            slot["name"] = fn["name"]
                        if fn.get("arguments"):
                            slot["arguments_fragments"].append(fn["arguments"])
                fr = choice.get("finish_reason")
                if fr:
                    flush()
                    if fr in ("stop", "tool_calls"):
                        events.append({"kind": "terminal", "status": "delivered", "text": "", "error": None})
                    else:
                        events.append({"kind": "terminal", "status": "errored", "text": "", "error": f"CHAT FINISH REASON: {fr}"})
        else:
            flush()
            events.extend(_chat_message(frame))

    flush()
    return events


def _valid_gate_evidence(evidence: Any) -> bool:
    return (isinstance(evidence, dict) and type(evidence.get("diff_bytes")) is int
            and evidence["diff_bytes"] > 0 and evidence.get("positive") is True
            and evidence.get("negative") is True and evidence.get("tree_restored") is True
            and type(evidence.get("exit_code")) is int and evidence["exit_code"] == 0
            and not evidence.get("timed_out"))


def _task_gate_evidence(frame: Dict[str, Any]) -> Optional[dict]:
    for artifact in frame.get("artifacts") or []:
        for part in artifact.get("parts") or []:
            data = part.get("data")
            if isinstance(data, dict) and _valid_gate_evidence(data.get("gate_evidence")):
                return data["gate_evidence"]
    return None


def _a2a(frame: Dict[str, Any]) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    if not isinstance(frame, dict):
        return []

    # Direct or embedded receipt check
    status = frame.get("status")
    if isinstance(status, str) and status.lower() == "received":
        loop_status = map_task_state("receipt", "received", "loop.v1")
        return [{"kind": "terminal", "status": loop_status, "text": "", "error": None}]
    if isinstance(frame.get("receipt"), dict):
        rec_status = frame["receipt"].get("status")
        if isinstance(rec_status, str) and rec_status.lower() == "received":
            loop_status = map_task_state("receipt", "received", "loop.v1")
            return [{"kind": "terminal", "status": loop_status, "text": "", "error": None}]

    # A2A Message: role, messageId, parts
    role_raw = frame.get("role")
    parts = frame.get("parts")
    if parts is not None or role_raw is not None or "messageId" in frame:
        role = "user" if str(role_raw or "").upper() in ("ROLE_USER", "USER") else "assistant"
        msg_id = frame.get("messageId", "")
        if isinstance(parts, list):
            for idx, p in enumerate(parts):
                if not isinstance(p, dict):
                    continue
                # A2A v1.0.1: parts discriminated by member presence, NO kind tag
                if "text" in p:
                    events.append({"kind": "text", "role": role, "text": str(p["text"]), "message_id": msg_id})
                elif "data" in p:
                    d = p["data"]
                    if isinstance(d, dict) and "tool_call" in d:
                        tc = d["tool_call"]
                        name = tc.get("name", "")
                        cid = tc.get("call_id") or tc.get("id") or f"call_{idx}"
                        args = _parse_arguments(tc.get("arguments", {}))
                        ev: Dict[str, Any] = {"kind": "tool_call", "call_id": cid, "name": name, "arguments": args}
                        if name.startswith("transfer_to_"):
                            ev["handoff_agent"] = name[len("transfer_to_"):]
                        events.append(ev)
                    elif isinstance(d, dict) and "tool_output" in d:
                        to = d["tool_output"]
                        cid = to.get("call_id") or to.get("id") or f"call_{idx}"
                        events.append({"kind": "tool_output", "call_id": cid, "output": to.get("output")})
                    else:
                        events.append({"kind": "tool_output", "call_id": f"data_{idx}", "output": d})

    # A2A Task: id/taskId, contextId, status.state, artifacts
    if isinstance(status, dict) and "state" in status:
        state = status.get("state")
        task_id = frame.get("id") or frame.get("taskId", "")
        ctx_id = frame.get("contextId", "")
        if state:
            loop_status = map_task_state("a2a", state, "loop.v1")
            evidence = _task_gate_evidence(frame)
            if loop_status == "delivered" and not evidence:
                loop_status = "unverified"
            events.append({
                "kind": "terminal",
                "status": loop_status,
                "text": "",
                "task_id": task_id,
                "context_id": ctx_id,
                "evidence": evidence,
                "error": None if loop_status == "delivered" else f"Task state: {state}",
            })
        for art in (frame.get("artifacts") or []):
            art_id = art.get("artifactId") or art.get("id", "")
            for p in (art.get("parts") or []):
                if isinstance(p, dict) and "data" in p:
                    if isinstance(p["data"], dict) and "gate_evidence" in p["data"]:
                        continue
                    events.append({"kind": "tool_output", "call_id": art_id or "artifact", "output": p["data"]})
                elif isinstance(p, dict) and "text" in p:
                    events.append({"kind": "text", "role": "assistant", "text": str(p["text"])})

    # A2A Task history recursion
    if isinstance(frame.get("history"), list):
        for h in frame["history"]:
            if isinstance(h, dict):
                events.extend(_a2a(h))

    return events


def _mcp_tasks(frame: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not isinstance(frame, dict):
        return []
    task_id = frame.get("taskId") or frame.get("id", "")
    status = frame.get("status")
    if not status:
        raise ValueError("MCP TASK STATUS MISSING")
    loop_status = map_task_state("mcp_tasks", status, "loop.v1")
    evidence = (frame.get("metadata") or {}).get("gate_evidence")
    if loop_status == "delivered" and not _valid_gate_evidence(evidence):
        loop_status = "unverified"
    return [{
        "kind": "terminal",
        "status": loop_status,
        "text": "",
        "task_id": task_id,
        "evidence": evidence,
        "error": None if loop_status == "delivered" else f"MCP task: {status}",
    }]


def _loop_v1(frame: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not isinstance(frame, dict):
        return []
    if "events" in frame and isinstance(frame["events"], list):
        return frame["events"]
    if "kind" in frame:
        return [frame]
    return []


def responses_items(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Project loop.v1 events into OpenAI Responses items."""
    items = []
    for ev in events:
        k = ev.get("kind")
        if k == "text":
            role = ev.get("role", "assistant")
            content_type = "output_text" if role == "assistant" else "input_text"
            items.append({
                "type": "message",
                "role": role,
                "content": [{"type": content_type, "text": ev.get("text", "")}],
            })
        elif k == "tool_call":
            name = ev.get("name", "")
            items.append({
                "type": "function_call",
                "call_id": ev.get("call_id", ""),
                "name": name,
                "arguments": json.dumps(ev.get("arguments", {}), ensure_ascii=False) if isinstance(ev.get("arguments"), dict) else str(ev.get("arguments", "{}")),
            })
        elif k == "tool_output":
            items.append({
                "type": "function_call_output",
                "call_id": ev.get("call_id", ""),
                "output": ev.get("output"),
            })
        elif k == "handoff":
            target = ev.get("target_agent", "")
            items.append({
                "type": "function_call",
                "call_id": ev.get("call_id", f"call_handoff_{target}"),
                "name": f"transfer_to_{target}",
                "arguments": json.dumps(ev.get("arguments", {}), ensure_ascii=False) if isinstance(ev.get("arguments"), dict) else str(ev.get("arguments", "{}")),
            })
    return items


def a2a_items(events: List[Dict[str, Any]], context_id: str = "") -> List[Dict[str, Any]]:
    """Project loop.v1 events into A2A v1.0.1 ProtoJSON items.

    Parts are discriminated strictly by member presence (text, data), NEVER kind tag.
    """
    items: List[Dict[str, Any]] = []
    idx = 0
    for ev in events:
        k = ev.get("kind")
        if k == "text":
            role = "ROLE_USER" if ev.get("role") == "user" else "ROLE_AGENT"
            items.append({
                "role": role,
                "messageId": ev.get("message_id") or f"msg_{idx}",
                "contextId": context_id or ev.get("context_id", ""),
                "parts": [{"text": ev.get("text", "")}],
            })
            idx += 1
        elif k == "tool_call":
            name = ev.get("name", "")
            items.append({
                "role": "ROLE_AGENT",
                "messageId": f"call_{ev.get('call_id', idx)}",
                "contextId": context_id or ev.get("context_id", ""),
                "parts": [{
                    "data": {
                        "tool_call": {
                            "name": name,
                            "call_id": ev.get("call_id", f"call_{idx}"),
                            "arguments": ev.get("arguments", {}),
                        }
                    }
                }],
            })
            idx += 1
        elif k == "tool_output":
            items.append({
                "role": "ROLE_AGENT",
                "messageId": f"out_{ev.get('call_id', idx)}",
                "contextId": context_id or ev.get("context_id", ""),
                "parts": [{
                    "data": {
                        "tool_output": {
                            "call_id": ev.get("call_id", f"call_{idx}"),
                            "output": ev.get("output"),
                        }
                    }
                }],
            })
            idx += 1
        elif k == "handoff":
            target = ev.get("target_agent", "")
            items.append({
                "role": "ROLE_AGENT",
                "messageId": f"handoff_{idx}",
                "contextId": context_id or ev.get("context_id", ""),
                "parts": [{
                    "data": {
                        "tool_call": {
                            "name": f"transfer_to_{target}",
                            "arguments": ev.get("arguments", {}),
                        }
                    }
                }],
                "metadata": {"agent": target},
            })
            idx += 1
        elif k == "terminal":
            loop_status = ev.get("status", "unverified")
            if loop_status == "delivered" and not _valid_gate_evidence(ev.get("evidence")):
                loop_status = "unverified"
            a2a_state = map_task_state("loop.v1", loop_status, "a2a")
            items.append({
                "id": ev.get("task_id") or str(uuid.uuid4()),
                "contextId": context_id or ev.get("context_id", ""),
                "status": {
                    "state": a2a_state,
                    "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                },
                "artifacts": ([{"artifactId": str(uuid.uuid4()),
                    "parts": [{"data": {"gate_evidence": ev["evidence"]}}]}]
                    if loop_status == "delivered" and ev.get("evidence") else []),
            })
    return items


def mcp_tasks_items(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Project loop.v1 events into MCP tasks extension items."""
    items: List[Dict[str, Any]] = []
    for ev in events:
        if ev.get("kind") == "terminal":
            loop_status = ev.get("status", "unverified")
            if loop_status == "delivered" and not _valid_gate_evidence(ev.get("evidence")):
                loop_status = "unverified"
            mcp_status = (None if loop_status in ("queued", "submitted") else
                          map_task_state("loop.v1", loop_status, "mcp_tasks"))
            if mcp_status:
                items.append({
                    "taskId": ev.get("task_id") or str(uuid.uuid4()),
                    "status": mcp_status,
                    "metadata": ({"gate_evidence": ev["evidence"]} if loop_status == "delivered" else {}),
                    "lastUpdatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                })
    return items


def loop_to_a2a(events: List[Dict[str, Any]], context_id: str = "") -> List[Dict[str, Any]]:
    """Bidirectional conversion: loop.v1 events -> A2A items."""
    return a2a_items(events, context_id=context_id)


def a2a_to_loop(frames: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Bidirectional conversion: A2A frames -> loop.v1 events."""
    events: List[Dict[str, Any]] = []
    for f in frames:
        events.extend(_a2a(f))
    return events


def loop_to_responses(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Bidirectional conversion: loop.v1 events -> OpenAI Responses items."""
    return responses_items(events)


def responses_to_loop(frames: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Bidirectional conversion: OpenAI Responses frames -> loop.v1 events."""
    events: List[Dict[str, Any]] = []
    for f in frames:
        events.extend(_openai(f))
    return events


def loop_to_mcp_tasks(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Bidirectional conversion: loop.v1 events -> MCP task items."""
    return mcp_tasks_items(events)


def mcp_tasks_to_loop(frames: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Bidirectional conversion: MCP task frames -> loop.v1 events."""
    events: List[Dict[str, Any]] = []
    for f in frames:
        events.extend(_mcp_tasks(f))
    return events


def translate(request: Dict[str, Any]) -> Dict[str, Any]:
    """Translate frames into loop.v1 events and standard projections."""
    if not isinstance(request, dict):
        raise ValueError("INVALID TRANSLATE REQUEST")
    source_raw = request.get("source", "")
    frames = request.get("frames")
    if not isinstance(frames, list):
        raise ValueError("INVALID TRANSLATE REQUEST: frames must be a list")

    known = {
        "auto", "agy", "claude", "openai", "openai_responses", "codex",
        "openai_chat", "chat_completions", "openai_compatible", "generic",
        "a2a", "mcp_tasks", "loop.v1",
    }
    if source_raw not in known:
        raise ValueError(f"UNKNOWN SOURCE: {source_raw}")

    # Credential key check across all frames
    for f in frames:
        key = credential_key(f)
        if key:
            raise ValueError(f"CREDENTIAL FIELD REFUSED: {key}")

    if source_raw == "auto":
        detected = None
        for f in frames:
            detected = detect_source(f)
            if detected:
                break
        if not detected:
            raise ValueError("AUTO DETECTION FAILED: no known frame shape")
        source = detected
    else:
        source = source_raw

    # Dialect translation
    if source in ("openai_chat", "chat_completions", "openai_compatible", "generic"):
        events = _openai_chat(frames)
    elif source == "agy":
        events = []
        for f in frames:
            events.extend(_agy(f))
    elif source == "claude":
        events = []
        for f in frames:
            events.extend(_claude(f))
    elif source in ("openai", "openai_responses", "codex"):
        events = []
        for f in frames:
            events.extend(_openai(f))
    elif source == "a2a":
        events = []
        for f in frames:
            events.extend(_a2a(f))
    elif source == "mcp_tasks":
        events = []
        for f in frames:
            events.extend(_mcp_tasks(f))
    elif source == "loop.v1":
        events = []
        for f in frames:
            events.extend(_loop_v1(f))
    else:
        raise ValueError(f"UNKNOWN SOURCE: {source}")

    # Ensure at least one Text event exists
    if not any(ev.get("kind") == "text" for ev in events):
        for ev in reversed(events):
            if ev.get("kind") == "terminal" and ev.get("text"):
                t_idx = next((i for i, e in enumerate(events) if e.get("kind") == "terminal"), len(events))
                events.insert(t_idx, {"kind": "text", "role": "assistant", "text": ev["text"]})
                break

    # Ensure terminal envelope
    if not any(ev.get("kind") == "terminal" for ev in events):
        events.append({"kind": "terminal", "status": "errored", "text": "", "error": "MISSING TERMINAL ENVELOPE"})

    # Denied action check
    denied = any(
        bool((f.get("result") or f).get("denied_actions") or (f.get("result") or f).get("permission_denials"))
        for f in frames if isinstance(f, dict)
    )

    evidence = request.get("evidence")
    for ev in events:
        if ev.get("kind") == "terminal" and ev.get("status") == "delivered":
            if denied:
                ev["status"] = "refused"
            elif (evidence := request.get("evidence") or ev.get("evidence")) is not None:
                if evidence.get("timed_out"):
                    ev["status"] = "timed_out"
                elif evidence.get("exit_code", 0) != 0:
                    ev["status"] = "errored"
                elif not evidence.get("tree_restored", True):
                    ev["status"] = "control_invalid"
                elif not evidence.get("positive") or not evidence.get("negative"):
                    ev["status"] = "gate_failed"
                elif evidence.get("diff_bytes", 0) == 0:
                    ev["status"] = "vacuous"
                elif not _valid_gate_evidence(evidence):
                    ev["status"] = "gate_failed"
                else:
                    ev["status"] = "delivered"
            else:
                ev["status"] = "unverified"
            if ev["status"] == "delivered":
                ev["evidence"] = dict(evidence)

    ctx_id = str(request.get("context_id") or "")
    return {
        "schema": "loop.v1",
        "events": events,
        "responses_items": responses_items(events),
        "a2a_items": a2a_items(events, context_id=ctx_id),
        "mcp_tasks": mcp_tasks_items(events),
        "otel_attributes": extract_otel_genai_attributes(frames) or build_otel_genai_attributes(conversation_id=ctx_id),
    }
