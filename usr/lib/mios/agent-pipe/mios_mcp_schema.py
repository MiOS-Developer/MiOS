#!/usr/bin/env python3
# AI-hint: Strict OpenAI tool schemas and caller-owned function/custom namespace projection.
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

    def __init__(self, message, *, param="tools"):
        super().__init__(message)
        self.param = param


class ClientToolResponseError(ValueError):
    """An engine returned a tool outside the caller's declared tool set."""


class _CustomToolFormat:
    """Validate official custom formats with the upstream Rust CFG engine."""

    def __init__(self, value):
        self.matcher = None
        if value is None:
            return
        if not isinstance(value, dict):
            raise ClientToolSchemaError("custom format must be an object")
        if value.get("type") == "text" and set(value) == {"type"}:
            return
        if value.get("type") != "grammar":
            raise ClientToolSchemaError("unsupported custom input format")
        nested = "grammar" in value
        grammar = value.get("grammar", value)
        expected = {"syntax", "definition"} if nested else {"type", "syntax", "definition"}
        if nested and set(value) != {"type", "grammar"}:
            raise ClientToolSchemaError("invalid custom grammar format fields")
        if (not isinstance(grammar, dict) or set(grammar) != expected
                or grammar.get("syntax") not in ("lark", "regex")
                or not isinstance(grammar.get("definition"), str)
                or not grammar["definition"].strip() or "\x00" in grammar["definition"]):
            raise ClientToolSchemaError("custom grammar requires a supported syntax and nonempty definition")
        try:
            from llguidance import LLMatcher, LLParserLimits, LLTokenizer, TokenizerWrapper
        except ImportError:
            raise ClientToolSchemaError("the declared grammar validator dependency is unavailable") from None

        class ByteTokenizer:
            # Grammar acceptance uses bytes, independent of the inference vocabulary.
            eos_token_id = 256
            bos_token_id = None
            tokens = [bytes([i]) for i in range(256)] + [b"<eos>"]
            special_token_ids = [256]

            def __call__(self, value):
                return list(value.encode("utf-8") if isinstance(value, str) else value)

        try:
            compiled = getattr(LLMatcher, "grammar_from_" + grammar["syntax"])(grammar["definition"])
            tokenizer = LLTokenizer(TokenizerWrapper(ByteTokenizer()), slices=[])
            self.matcher = LLMatcher(tokenizer, compiled, log_level=0,
                                     limits=LLParserLimits(verbose_errors=False))
            if self.matcher.is_error():
                raise ValueError("invalid grammar")
        except Exception:
            raise ClientToolSchemaError("custom grammar is invalid, unsupported or exceeds parser limits") from None

    def accepts(self, value):
        if not isinstance(value, str):
            return False
        try:
            data = value.encode("utf-8")
        except UnicodeError:
            return False
        if self.matcher is None:
            return True
        matcher = self.matcher.deep_copy()
        return matcher.consume_tokens(list(data)) and matcher.is_accepting()


class ClientToolProjection:
    """Request-local namespace projection for engines accepting flat functions.

    No tools are executed here. Caller schemas remain untouched; engine names are
    stable over the namespace/name pair and cannot collide with host-side verbs.
    Tool type, namespace and raw custom input survive both API surfaces.
    Custom grammars use the upstream Rust CFG engine; tools are never executed.
    """

    def __init__(self, body: dict):
        import copy
        import hashlib
        import json
        import re
        self.body = copy.deepcopy(body)
        self.names, self.reverse, self.kinds, self.formats = {}, {}, {}, {}
        self.stream_calls = {}
        self.stream_choices, self.stream_finished = set(), set()
        groups, flat = set(), []
        tools = body.get("tools")
        if not isinstance(tools, list) or not tools:
            raise ClientToolSchemaError("tools must be a non-empty array")
        for tool_index, tool in enumerate(tools):
            if not isinstance(tool, dict):
                raise ClientToolSchemaError("each tool must be an object")
            namespace, children = None, [tool]
            if tool.get("type") == "namespace":
                namespace = tool.get("name")
                if (not isinstance(namespace, str) or not namespace.strip()
                        or any(ord(c) < 32 for c in namespace) or namespace in groups):
                    raise ClientToolSchemaError("namespace names must be unique non-empty strings")
                groups.add(namespace)
                children = tool.get("tools")
                if not isinstance(children, list) or not children:
                    raise ClientToolSchemaError("a namespace must contain function or custom tools")
            for child_index, child in enumerate(children):
                kind = child.get("type") if isinstance(child, dict) else None
                if kind not in ("function", "custom"):
                    # Report only a bounded protocol tag and numeric location:
                    # never serialize the definition, prompt, name or headers.
                    label = kind if isinstance(kind, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", kind) else "<invalid>"
                    param = f"tools[{tool_index}]"
                    if namespace is not None:
                        param += f".tools[{child_index}]"
                    raise ClientToolSchemaError(
                        f"unsupported caller tool type '{label}'; this engine accepts function/custom tools",
                        param=param + ".type")
                definition = child.get(kind, child)
                if not isinstance(definition, dict):
                    raise ClientToolSchemaError("tool definition must be an object")
                if definition.get("description") is not None and not isinstance(definition["description"], str):
                    raise ClientToolSchemaError("tool description must be a string")
                name = definition.get("name")
                if not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z0-9_.-]{1,128}", name):
                    raise ClientToolSchemaError("tool names must be valid OpenAI identifiers")
                key = (namespace, name)
                if key in self.reverse:
                    raise ClientToolSchemaError("duplicate tool in the same namespace")
                if (definition.get("defer_loading") or definition.get("async")
                        or definition.get("allowed_callers") not in (None, ["direct"])):
                    raise ClientToolSchemaError("this engine cannot preserve deferred, asynchronous or programmatic tools")
                # Keep the caller verb recognizable to the engine while a
                # 128-bit identity suffix separates namespaces and truncated names.
                stem = re.sub(r"[^a-zA-Z0-9_-]", "_", name)[:21]
                alias = "mios_tool_" + stem + "_" + hashlib.sha256(json.dumps(key).encode()).hexdigest()[:32]
                if alias in self.names:
                    raise ClientToolSchemaError("tool projection identity collision")
                self.names[alias], self.reverse[key], self.kinds[alias] = key, alias, kind
                if kind == "custom":
                    self.formats[alias] = _CustomToolFormat(definition.get("format"))
                    projected = {
                        "name": alias, "description": definition.get("description") or name,
                        "strict": True, "parameters": {"type": "object",
                            "properties": {"input": {"type": "string"}},
                            "required": ["input"], "additionalProperties": False}}
                    if definition.get("format"):
                        projected["description"] += "\nThe input must satisfy this custom format: " + json.dumps(
                            definition["format"], ensure_ascii=False)
                else:
                    parameters = definition.get("parameters", {"type": "object", "properties": {}})
                    self._validate_schema(parameters, root=True)
                    projected = {k: copy.deepcopy(v) for k, v in definition.items()
                                 if k in ("description", "parameters", "strict")}
                    projected["name"] = alias
                    projected.setdefault("parameters", copy.deepcopy(parameters))
                if namespace:
                    context = tool.get("description") or namespace
                    projected["description"] = f"{namespace}: {context}\n{projected.get('description') or name}"
                identity = json.dumps({"namespace": namespace, "name": name}, ensure_ascii=False)
                projected["description"] = f"Caller tool {identity}\n{projected.get('description') or name}"
                flat.append({"type": "function", "function": projected})
        self.body["tools"] = flat
        if "tool_choice" in self.body:
            self.body["tool_choice"] = self._choice(self.body["tool_choice"])
        for msg in self.body.get("messages") or []:
            if not isinstance(msg, dict):
                raise ClientToolSchemaError("messages must contain objects")
            for call in msg.get("tool_calls") or []:
                if not isinstance(call, dict) or call.get("type", "function") not in ("function", "custom"):
                    raise ClientToolSchemaError("history contains an invalid tool call")
                kind = call.get("type", "function")
                payload = call.get(kind)
                if not isinstance(payload, dict):
                    raise ClientToolSchemaError("history contains an invalid tool call")
                if ("namespace" in call and "namespace" in payload
                        and call["namespace"] != payload["namespace"]):
                    raise ClientToolSchemaError("history contains conflicting namespaces")
                ns = call.pop("namespace", payload.get("namespace"))
                payload.pop("namespace", None)
                alias = self._alias(payload.get("name"), ns)
                if self.kinds[alias] != kind:
                    raise ClientToolSchemaError("history tool type does not match its declaration")
                if kind == "custom":
                    value = payload.get("input")
                    if not self.formats[alias].accepts(value):
                        raise ClientToolSchemaError("history custom input does not satisfy its declared format")
                    call.pop("custom")
                    call.update(type="function", function={"name": alias,
                        "arguments": json.dumps({"input": value}, ensure_ascii=False)})
                else:
                    payload["name"] = alias

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
            raise ClientToolSchemaError("tool name is required")
        if namespace is not None:
            if not isinstance(namespace, str) or (namespace, name) not in self.reverse:
                raise ClientToolSchemaError("unknown namespace or tool")
            return self.reverse[(namespace, name)]
        if (None, name) in self.reverse:
            return self.reverse[(None, name)]
        qualified = [alias for (ns, leaf), alias in self.reverse.items()
                     if ns and f"{ns}.{leaf}" == name]
        if len(qualified) == 1:
            return qualified[0]
        raise ClientToolSchemaError("tool must identify its declared namespace")

    def _choice(self, choice):
        if isinstance(choice, str) and choice in ("none", "auto", "required"):
            return choice
        if not isinstance(choice, dict):
            raise ClientToolSchemaError("unsupported tool_choice")
        if choice.get("type") == "allowed_tools":
            allowed = choice.get("allowed_tools", choice)
            if not isinstance(allowed, dict) or allowed.get("mode") not in ("auto", "required"):
                raise ClientToolSchemaError("invalid allowed_tools choice")
            tools = allowed.get("tools")
            if not isinstance(tools, list) or not tools:
                raise ClientToolSchemaError("allowed_tools must contain declared tools")
            return {"type": "allowed_tools", "allowed_tools": {
                "mode": allowed["mode"], "tools": [self._choice(t) for t in tools]}}
        kind = choice.get("type")
        if kind not in ("function", "custom"):
            raise ClientToolSchemaError("tool_choice must target a function or custom tool")
        payload = choice.get(kind, choice)
        if not isinstance(payload, dict):
            raise ClientToolSchemaError("invalid tool choice")
        if ("namespace" in payload and "namespace" in choice
                and payload["namespace"] != choice["namespace"]):
            raise ClientToolSchemaError("tool_choice contains conflicting namespaces")
        namespace = payload.get("namespace", choice.get("namespace"))
        alias = self._alias(payload.get("name"), namespace)
        if self.kinds[alias] != kind:
            raise ClientToolSchemaError("tool_choice type does not match its declaration")
        return {"type": "function", "function": {"name": alias}}

    def _custom_input(self, alias, arguments):
        import json
        def pairs(items):
            result = {}
            for name, value in items:
                if name in result:
                    raise ValueError("duplicate custom input field")
                result[name] = value
            return result
        try:
            value = json.loads(arguments, object_pairs_hook=pairs)
            if not isinstance(value, dict) or set(value) != {"input"}:
                raise ValueError("invalid custom input wrapper")
            value = value["input"]
            if not self.formats[alias].accepts(value):
                raise ValueError("custom input violates its declared format")
            return value
        except Exception:
            raise ClientToolResponseError("engine custom input does not satisfy its declared format") from None

    def restore_call(self, call):
        import copy
        result = copy.deepcopy(call)
        fn = result.get("function")
        if (result.get("type", "function") != "function" or not isinstance(fn, dict)
                or fn.get("name") not in self.names):
            raise ClientToolResponseError("engine returned an undeclared function")
        alias = fn["name"]
        namespace, name = self.names[alias]
        if self.kinds[alias] == "custom":
            value = self._custom_input(alias, fn.get("arguments"))
            result.pop("function")
            result.update(type="custom", custom={"name": name, "input": value})
        else:
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
                calls = [self.restore_call(c) for c in message["tool_calls"]]
                if any(c.get("type") == "custom" for c in calls) and choice.get("finish_reason") != "tool_calls":
                    raise ClientToolResponseError("engine did not acknowledge completion of its custom tool call")
                message["tool_calls"] = calls
        return result

    def restore_chunk(self, chunk):
        """Function deltas pass through; custom inputs wait for complete validated JSON."""
        import copy
        result = copy.deepcopy(chunk)
        for choice in result.get("choices") or []:
            choice_index = choice.get("index", 0)
            if choice_index in self.stream_finished:
                raise ClientToolResponseError("engine emitted tool data after completion")
            self.stream_choices.add(choice_index)
            finished = choice.get("finish_reason") is not None
            if finished:
                self.stream_finished.add(choice_index)
            delta = choice.get("delta") or {}
            calls = []
            for call in delta.get("tool_calls") or []:
                key = (choice_index, call.get("index", 0))
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
                    if not isinstance(fn["arguments"], str):
                        raise ClientToolResponseError("engine streamed malformed tool arguments")
                    pending_fn["arguments"] = pending_fn.get("arguments", "") + fn["arguments"]
                if state["name"] in self.names:
                    pending_fn["name"] = state["name"]
                    if self.kinds[state["name"]] == "function":
                        calls.append(self.restore_call(pending))
                        state["sent"] = True
                elif not any(name.startswith(state["name"]) for name in self.names):
                    raise ClientToolResponseError("engine streamed an undeclared function")
            if finished:
                for key, state in self.stream_calls.items():
                    if key[0] != choice_index or state["sent"]:
                        continue
                    alias = state["name"]
                    if alias not in self.names:
                        raise ClientToolResponseError("engine ended before identifying a declared tool")
                    if self.kinds[alias] != "custom" or choice["finish_reason"] != "tool_calls":
                        raise ClientToolResponseError("engine did not complete its custom tool call")
                    calls.append(self.restore_call(state["call"]))
                    state["sent"] = True
            if "tool_calls" in delta or calls:
                if calls:
                    delta["tool_calls"] = calls
                    choice["delta"] = delta
                else:
                    delta.pop("tool_calls", None)
        return result

    def validate_stream_end(self):
        if (not self.stream_choices or self.stream_choices != self.stream_finished
                or any(not state["sent"] for state in self.stream_calls.values())):
            raise ClientToolResponseError("tool stream ended without a completion acknowledgment")

    async def restore_sse(self, lines, model):
        """Translate SSE while validating custom formats off the event loop."""
        import asyncio
        import json
        data, done = [], False
        async def frame():
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
            mapped = await asyncio.to_thread(self.restore_chunk, chunk)
            mapped["model"] = model
            return ("data: " + json.dumps(mapped) + "\n\n").encode()
        async for line in lines:
            if line.startswith("data:"):
                data.append(line[5:].lstrip(" "))
            elif line == "":
                value = await frame()
                if value:
                    yield value
                if done:
                    return
        if data:
            value = await frame()
            if value:
                yield value
        self.validate_stream_end()
