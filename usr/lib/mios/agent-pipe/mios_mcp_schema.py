#!/usr/bin/env python3
# AI-hint: Strict OpenAI function-schema conversion for MCP tool definitions.
# AI-related: mios_mcp, mios_mcp_transport
"""Pure schema translation, split out of mios_mcp.py.

No module state: these are total functions over dicts, which is why they were
the first thing to lift when mios_mcp.py went past the 800-line ceiling.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

# ---------------------------------------------------------------------------
# Strict OpenAI Function Schema Conversion
# ---------------------------------------------------------------------------

def make_schema_strict(schema: Optional[dict]) -> dict:
    """Convert an arbitrary JSON Schema (such as an MCP tool inputSchema) into
    a strict OpenAI function parameter definition.
    - Ensures root type is 'object'
    - Ensures all properties are listed in 'required'
    - Widens optional properties with 'null' type
    - Sets 'additionalProperties: False'
    - Recursively processes nested objects and arrays."""
    if not isinstance(schema, dict) or not schema:
        return {"type": "object", "properties": {}, "required": [], "additionalProperties": False}

    s = dict(schema)
    stype = s.get("type")

    if stype == "object" or ("properties" in s) or stype is None:
        s["type"] = "object"
        properties = s.get("properties") or {}
        if not isinstance(properties, dict):
            properties = {}
        s["properties"] = dict(properties)

        required = s.get("required") or []
        if not isinstance(required, list):
            required = []
        new_required = list(required)

        for prop_name, prop_val in s["properties"].items():
            if isinstance(prop_val, dict):
                prop_val = make_schema_strict(prop_val)
                s["properties"][prop_name] = prop_val
                if prop_name not in required:
                    new_required.append(prop_name)
                    t = prop_val.get("type")
                    if isinstance(t, str):
                        prop_val["type"] = [t, "null"]
                    elif isinstance(t, list):
                        if "null" not in t:
                            prop_val["type"] = list(t) + ["null"]
                    else:
                        prop_val["type"] = ["object", "null"]
            else:
                prop_val = {"type": ["string", "null"]}
                s["properties"][prop_name] = prop_val
                if prop_name not in required:
                    new_required.append(prop_name)

        s["required"] = new_required
        s["additionalProperties"] = False

    elif stype == "array":
        items = s.get("items")
        if isinstance(items, dict):
            s["items"] = make_schema_strict(items)

    return s

# Alias for backward compatibility
_make_schema_strict = make_schema_strict

def convert_mcp_to_openai_schema(mcp_tool: dict, server_id: str = "") -> dict:
    """Convert an MCP tool definition (name, description, inputSchema) into a
    strict OpenAI function tool schema:
    {"type": "function", "function": {"name": ..., "description": ..., "parameters": ..., "strict": True}}"""
    raw_name = str(mcp_tool.get("name") or "").strip()
    if server_id and not raw_name.startswith(f"mcp.{server_id}."):
        if raw_name.startswith("mcp."):
            tool_name = raw_name
        else:
            tool_name = f"mcp.{server_id}.{raw_name}"
    else:
        tool_name = raw_name

    desc = mcp_tool.get("description") or f"MCP tool {tool_name}"
    input_schema = mcp_tool.get("inputSchema") or {}
    strict_parameters = make_schema_strict(input_schema)

    schema: Dict[str, Any] = {
        "type": "function",
        "function": {
            "name": tool_name,
            "description": desc,
            "strict": True,
            "parameters": strict_parameters,
        },
    }
    sid = server_id or mcp_tool.get("server_id")
    if sid:
        schema["x-mios-mcp-server"] = sid
    return schema

# Alias for backward compatibility with existing agent-pipe modules
_mcp_tool_to_openai_tool = lambda key, info: convert_mcp_to_openai_schema(
    {"name": key, "description": info.get("description"), "inputSchema": info.get("inputSchema")},
    server_id=info.get("server_id", ""),
)



class ClientToolSchemaError(ValueError):
    """A caller-owned tool cannot be represented without changing its contract."""


class ClientToolResponseError(ValueError):
    """An engine returned a tool outside the caller's declared tool set."""


class ClientToolProjection:
    """Request-local namespace projection for engines accepting flat functions.

    No tools are executed here. Caller schemas remain untouched; engine names are
    stable over the namespace/name pair and cannot collide with host-side verbs.
    Namespace is preserved on Chat bridge calls and Responses function_call items.
    """

    def __init__(self, body: dict):
        import copy
        import hashlib
        import json
        import re
        self.body = copy.deepcopy(body)
        self.names = {}
        self.reverse = {}
        self.stream_calls = {}
        self.stream_choices = set()
        self.stream_finished = set()
        groups = set()
        flat = []
        tools = body.get("tools")
        if not isinstance(tools, list) or not tools:
            raise ClientToolSchemaError("tools must be a non-empty array")
        for tool in tools:
            if not isinstance(tool, dict):
                raise ClientToolSchemaError("each tool must be an object")
            namespace = None
            children = [tool]
            if tool.get("type") == "namespace":
                namespace = tool.get("name")
                if (not isinstance(namespace, str) or not namespace.strip()
                        or any(ord(c) < 32 for c in namespace) or namespace in groups):
                    raise ClientToolSchemaError("namespace names must be unique non-empty strings")
                groups.add(namespace)
                children = tool.get("tools")
                if not isinstance(children, list) or not children:
                    raise ClientToolSchemaError("a namespace must contain function tools")
            for child in children:
                if not isinstance(child, dict) or child.get("type") != "function":
                    raise ClientToolSchemaError("this engine supports only function tools inside namespaces")
                fn = child.get("function", child)
                if not isinstance(fn, dict):
                    raise ClientToolSchemaError("function definition must be an object")
                name = fn.get("name")
                if not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z0-9_.-]{1,128}", name):
                    raise ClientToolSchemaError("function names must be valid OpenAI identifiers")
                key = (namespace, name)
                if key in self.reverse:
                    raise ClientToolSchemaError("duplicate function in the same namespace")
                if fn.get("defer_loading") or fn.get("async") or fn.get("allowed_callers") not in (None, ["direct"]):
                    raise ClientToolSchemaError("this engine cannot preserve deferred, asynchronous or programmatic tools")
                parameters = fn.get("parameters", {"type": "object", "properties": {}})
                self._validate_schema(parameters, root=True)
                alias = "mios_tool_" + hashlib.sha256(json.dumps(key).encode()).hexdigest()[:54]
                if alias in self.names:
                    raise ClientToolSchemaError("tool projection identity collision")
                self.names[alias] = key
                self.reverse[key] = alias
                projected = {k: copy.deepcopy(v) for k, v in fn.items()
                             if k in ("description", "parameters", "strict")}
                projected["name"] = alias
                projected.setdefault("parameters", copy.deepcopy(parameters))
                if namespace:
                    context = tool.get("description") or namespace
                    projected["description"] = f"{namespace}: {context}\n{fn.get('description') or name}"
                flat.append({"type": "function", "function": projected})
        self.body["tools"] = flat
        if "tool_choice" in self.body:
            self.body["tool_choice"] = self._choice(self.body["tool_choice"])
        for msg in self.body.get("messages") or []:
            if not isinstance(msg, dict):
                raise ClientToolSchemaError("messages must contain objects")
            for call in msg.get("tool_calls") or []:
                if not isinstance(call, dict) or not isinstance(call.get("function"), dict):
                    raise ClientToolSchemaError("history contains an invalid function call")
                fn = call["function"]
                if ("namespace" in call and "namespace" in fn
                        and call["namespace"] != fn["namespace"]):
                    raise ClientToolSchemaError("history contains conflicting namespaces")
                ns = call.pop("namespace", fn.get("namespace"))
                fn.pop("namespace", None)
                fn["name"] = self._alias(fn.get("name"), ns)

    @staticmethod
    def _validate_schema(schema, *, root=False):
        if not isinstance(schema, dict):
            raise ClientToolSchemaError("function parameters must be a JSON Schema object")
        valid_types = {"object", "array", "string", "number", "integer", "boolean", "null"}
        kind = schema.get("type")
        if (kind is not None and not (
                isinstance(kind, str) and kind in valid_types
                or isinstance(kind, list) and kind and all(isinstance(k, str) and k in valid_types for k in kind))):
            raise ClientToolSchemaError("invalid JSON Schema type")
        if root and kind not in (None, "object"):
            raise ClientToolSchemaError("function parameters must describe an object")
        props = schema.get("properties", {})
        if not isinstance(props, dict):
            raise ClientToolSchemaError("JSON Schema properties must be an object")
        for value in props.values():
            ClientToolProjection._validate_schema(value)
        required = schema.get("required", [])
        if (not isinstance(required, list) or any(not isinstance(k, str) for k in required)
                or len(set(required)) != len(required) or any(k not in props for k in required)):
            raise ClientToolSchemaError("JSON Schema required must name declared properties")
        for keyword in ("allOf", "anyOf", "oneOf"):
            if keyword in schema:
                value = schema[keyword]
                if not isinstance(value, list) or not value:
                    raise ClientToolSchemaError(f"JSON Schema {keyword} must be a non-empty array")
                for item in value:
                    ClientToolProjection._validate_schema(item)
        for keyword in ("items", "not"):
            if keyword in schema:
                ClientToolProjection._validate_schema(schema[keyword])
        for keyword in ("$defs", "definitions"):
            if keyword in schema:
                if not isinstance(schema[keyword], dict):
                    raise ClientToolSchemaError(f"JSON Schema {keyword} must be an object")
                for value in schema[keyword].values():
                    ClientToolProjection._validate_schema(value)
        if "additionalProperties" in schema and not isinstance(schema["additionalProperties"], (bool, dict)):
            raise ClientToolSchemaError("invalid JSON Schema additionalProperties")
        if isinstance(schema.get("additionalProperties"), dict):
            ClientToolProjection._validate_schema(schema["additionalProperties"])
        if "$ref" in schema and not isinstance(schema["$ref"], str):
            raise ClientToolSchemaError("JSON Schema reference must be a string")

    def _alias(self, name, namespace=None):
        if not isinstance(name, str) or not name:
            raise ClientToolSchemaError("function name is required")
        if namespace is not None:
            if not isinstance(namespace, str) or (namespace, name) not in self.reverse:
                raise ClientToolSchemaError("unknown namespace or function")
            return self.reverse[(namespace, name)]
        if (None, name) in self.reverse:
            return self.reverse[(None, name)]
        qualified = [alias for (ns, leaf), alias in self.reverse.items()
                     if ns and f"{ns}.{leaf}" == name]
        if len(qualified) == 1:
            return qualified[0]
        raise ClientToolSchemaError("function must identify its declared namespace")

    def _choice(self, choice):
        if isinstance(choice, str) and choice in ("none", "auto", "required"):
            return choice
        if not isinstance(choice, dict):
            raise ClientToolSchemaError("unsupported tool_choice")
        if choice.get("type") == "allowed_tools":
            allowed = choice.get("allowed_tools")
            if not isinstance(allowed, dict) or allowed.get("mode") not in ("auto", "required"):
                raise ClientToolSchemaError("invalid allowed_tools choice")
            tools = allowed.get("tools")
            if not isinstance(tools, list) or not tools:
                raise ClientToolSchemaError("allowed_tools must contain declared functions")
            return {"type": "allowed_tools", "allowed_tools": {
                "mode": allowed["mode"], "tools": [self._choice(t) for t in tools]}}
        if choice.get("type") != "function":
            raise ClientToolSchemaError("tool_choice must target a function, not a namespace")
        fn = choice.get("function", choice)
        if not isinstance(fn, dict):
            raise ClientToolSchemaError("invalid function choice")
        namespace = fn.get("namespace", choice.get("namespace"))
        return {"type": "function", "function": {"name": self._alias(fn.get("name"), namespace)}}

    def restore_call(self, call):
        import copy
        result = copy.deepcopy(call)
        fn = result.get("function")
        if not isinstance(fn, dict) or fn.get("name") not in self.names:
            raise ClientToolResponseError("engine returned an undeclared function")
        namespace, name = self.names[fn["name"]]
        fn["name"] = name
        fn.pop("namespace", None)
        result.pop("namespace", None)
        if namespace is not None:
            result["namespace"] = namespace
        return result

    def restore_completion(self, response):
        import copy
        result = copy.deepcopy(response)
        if not isinstance(result, dict) or not isinstance(result.get("choices"), list) or not result["choices"]:
            raise ClientToolResponseError("engine returned no completion")
        for choice in result["choices"]:
            message = choice.get("message")
            if not isinstance(message, dict) or not (message.get("tool_calls")
                    or isinstance(message.get("content"), str) and message["content"].strip()):
                raise ClientToolResponseError("engine returned no assistant output")
            if "tool_calls" in message:
                message["tool_calls"] = [self.restore_call(c) for c in message["tool_calls"]]
        return result

    def restore_chunk(self, chunk):
        """Buffer fragmented engine names until a declared alias is identified."""
        import copy
        result = copy.deepcopy(chunk)
        for choice in result.get("choices") or []:
            self.stream_choices.add(choice.get("index", 0))
            if choice.get("finish_reason") is not None:
                self.stream_finished.add(choice.get("index", 0))
            delta = choice.get("delta") or {}
            calls = []
            for call in delta.get("tool_calls") or []:
                key = (choice.get("index", 0), call.get("index", 0))
                state = self.stream_calls.setdefault(key, {"name": "", "sent": False, "call": {}})
                fn = call.get("function") or {}
                if state["sent"]:
                    if fn.get("name"):
                        raise ClientToolResponseError("engine changed an emitted function name")
                    calls.append(call)
                    continue
                pending = state["call"]
                pending.update({k: v for k, v in call.items() if k != "function"})
                pending_fn = pending.setdefault("function", {})
                state["name"] += fn.get("name") or ""
                if "arguments" in fn:
                    pending_fn["arguments"] = pending_fn.get("arguments", "") + fn["arguments"]
                if state["name"] in self.names:
                    pending_fn["name"] = state["name"]
                    calls.append(self.restore_call(pending))
                    state["sent"] = True
                elif not any(name.startswith(state["name"]) for name in self.names):
                    raise ClientToolResponseError("engine streamed an undeclared function")
            if "tool_calls" in delta:
                if calls:
                    delta["tool_calls"] = calls
                else:
                    delta.pop("tool_calls")
            if choice.get("finish_reason") is not None and any(
                    not state["sent"] for key, state in self.stream_calls.items()
                    if key[0] == choice.get("index", 0)):
                raise ClientToolResponseError("engine ended before identifying a declared function")
        return result


    def validate_stream_end(self):
        if (not self.stream_choices or self.stream_choices != self.stream_finished
                or any(not state["sent"] for state in self.stream_calls.values())):
            raise ClientToolResponseError("tool stream ended without a completion acknowledgment")


    async def restore_sse(self, lines, model):
        """Translate decoded SSE lines without owning the HTTP connection."""
        import json
        data = []
        done = False
        def frame():
            nonlocal done
            value = "\n".join(data)
            data.clear()
            if not value:
                return None
            if value.strip() == "[DONE]":
                self.validate_stream_end()
                done = True
                return b"data: [DONE]\n\n"
            chunk = json.loads(value)
            if "error" in chunk:
                raise ClientToolResponseError("engine returned a stream error")
            mapped = self.restore_chunk(chunk)
            mapped["model"] = model
            return ("data: " + json.dumps(mapped) + "\n\n").encode()
        async for line in lines:
            if line.startswith("data:"):
                data.append(line[5:].lstrip(" "))
            elif line == "":
                value = frame()
                if value:
                    yield value
                if done:
                    return
        if data:
            value = frame()
            if value:
                yield value
        self.validate_stream_end()
