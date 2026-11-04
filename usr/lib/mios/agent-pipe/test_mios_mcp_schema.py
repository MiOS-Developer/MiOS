#!/usr/bin/env python3
# AI-hint: Stdlib unit test for the strict OpenAI function-schema conversion of MCP tools (mios_mcp_schema).
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md
"""Unit tests for mios_mcp_schema: the strict OpenAI function-schema contract."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mios_mcp_schema as S


class StrictSchemaTests(unittest.TestCase):
    def test_empty_or_non_dict_yields_a_valid_empty_object(self):
        # A tool with no inputSchema still has to produce a schema the API accepts.
        for bad in ({}, None, [], "nope", 7):
            out = S.make_schema_strict(bad)
            self.assertEqual(out["type"], "object")
            self.assertEqual(out["properties"], {})
            self.assertEqual(out["required"], [])
            self.assertIs(out["additionalProperties"], False)

    def test_every_property_becomes_required(self):
        out = S.make_schema_strict({"type": "object", "properties": {"a": {"type": "string"},
                                                                    "b": {"type": "integer"}}})
        self.assertEqual(sorted(out["required"]), ["a", "b"])
        self.assertIs(out["additionalProperties"], False)

    def test_an_optional_property_is_widened_with_null(self):
        # Strict mode requires every key; optionality is expressed as a null union.
        out = S.make_schema_strict({"type": "object",
                                    "properties": {"a": {"type": "string"}, "b": {"type": "string"}},
                                    "required": ["a"]})
        self.assertEqual(out["properties"]["a"]["type"], "string", "already-required stays narrow")
        self.assertEqual(out["properties"]["b"]["type"], ["string", "null"])

    def test_a_union_typed_optional_gains_null_once(self):
        out = S.make_schema_strict({"properties": {"b": {"type": ["string", "integer"]}}})
        self.assertEqual(out["properties"]["b"]["type"], ["string", "integer", "null"])
        out2 = S.make_schema_strict({"properties": {"b": {"type": ["string", "null"]}}})
        self.assertEqual(out2["properties"]["b"]["type"], ["string", "null"],
                         "null must not be appended twice")

    def test_a_typeless_optional_becomes_object_null(self):
        out = S.make_schema_strict({"properties": {"b": {"description": "no type"}}})
        self.assertEqual(out["properties"]["b"]["type"], ["object", "null"])

    def test_a_non_dict_property_value_is_replaced_not_crashed(self):
        out = S.make_schema_strict({"properties": {"b": "garbage"}})
        self.assertEqual(out["properties"]["b"], {"type": ["string", "null"]})
        self.assertIn("b", out["required"])

    def test_malformed_properties_and_required_do_not_propagate(self):
        out = S.make_schema_strict({"type": "object", "properties": "nope", "required": "nope"})
        self.assertEqual(out["properties"], {})
        self.assertEqual(out["required"], [])

    def test_recursion_reaches_nested_objects(self):
        out = S.make_schema_strict({"properties": {"outer": {"type": "object",
                                                             "properties": {"inner": {"type": "string"}}}}})
        inner = out["properties"]["outer"]
        self.assertIs(inner["additionalProperties"], False, "nested object must be strict too")
        self.assertIn("inner", inner["required"])

    def test_recursion_reaches_array_items(self):
        out = S.make_schema_strict({"type": "array",
                                    "items": {"type": "object", "properties": {"x": {"type": "string"}}}})
        self.assertEqual(out["type"], "array")
        self.assertIs(out["items"]["additionalProperties"], False)
        self.assertIn("x", out["items"]["required"])

    def test_an_array_with_non_dict_items_is_left_alone(self):
        out = S.make_schema_strict({"type": "array", "items": "nope"})
        self.assertEqual(out["items"], "nope")

    def test_the_input_is_not_mutated(self):
        # These are called on tool definitions held in a registry; mutating the
        # caller's dict would corrupt the next conversion.
        src = {"type": "object", "properties": {"a": {"type": "string"}}}
        S.make_schema_strict(src)
        self.assertEqual(src, {"type": "object", "properties": {"a": {"type": "string"}}})


class ToolConversionTests(unittest.TestCase):
    def test_shape_is_an_openai_function_tool(self):
        out = S.convert_mcp_to_openai_schema({"name": "read", "description": "d",
                                              "inputSchema": {"properties": {"p": {"type": "string"}}}})
        self.assertEqual(out["type"], "function")
        fn = out["function"]
        self.assertEqual(fn["name"], "read")
        self.assertEqual(fn["description"], "d")
        self.assertIs(fn["strict"], True)
        self.assertIs(fn["parameters"]["additionalProperties"], False)

    def test_server_id_namespaces_the_tool_name(self):
        out = S.convert_mcp_to_openai_schema({"name": "read"}, server_id="fs")
        self.assertEqual(out["function"]["name"], "mcp.fs.read")
        self.assertEqual(out["x-mios-mcp-server"], "fs")

    def test_an_already_namespaced_name_is_not_double_prefixed(self):
        self.assertEqual(
            S.convert_mcp_to_openai_schema({"name": "mcp.fs.read"}, server_id="fs")["function"]["name"],
            "mcp.fs.read")
        self.assertEqual(
            S.convert_mcp_to_openai_schema({"name": "mcp.other.read"}, server_id="fs")["function"]["name"],
            "mcp.other.read", "an mcp.* name from another server is left as-is")

    def test_a_missing_description_gets_a_derived_one(self):
        out = S.convert_mcp_to_openai_schema({"name": "read"}, server_id="fs")
        self.assertEqual(out["function"]["description"], "MCP tool mcp.fs.read")

    def test_server_id_falls_back_to_the_tool_payload(self):
        out = S.convert_mcp_to_openai_schema({"name": "read", "server_id": "fs"})
        self.assertEqual(out["x-mios-mcp-server"], "fs")
        self.assertEqual(out["function"]["name"], "read",
                         "the payload's server_id annotates, it does not rename")

    def test_no_server_id_leaves_the_annotation_off(self):
        self.assertNotIn("x-mios-mcp-server", S.convert_mcp_to_openai_schema({"name": "read"}))

    def test_a_missing_input_schema_still_yields_strict_parameters(self):
        params = S.convert_mcp_to_openai_schema({"name": "read"})["function"]["parameters"]
        self.assertEqual(params, {"type": "object", "properties": {}, "required": [],
                                  "additionalProperties": False})

    def test_the_back_compat_aliases_are_the_same_functions(self):
        # mios_mcp.py still imports these names; a rename that dropped them would
        # break at import time in production but pass every test above.
        self.assertIs(S._make_schema_strict, S.make_schema_strict)
        out = S._mcp_tool_to_openai_tool("read", {"description": "d", "server_id": "fs",
                                                  "inputSchema": {"properties": {"p": {"type": "string"}}}})
        self.assertEqual(out["function"]["name"], "mcp.fs.read")
        self.assertEqual(out["function"]["description"], "d")



class NamespaceProjectionTests(unittest.TestCase):
    def body(self):
        return {"tools": [{"type": "namespace", "name": ns, "description": ns + " tools",
                          "tools": [{"type": "function", "name": "search",
                                     "parameters": {"type": "object", "properties": {
                                         "q": {"type": "string"}}, "required": ["q"]}, "strict": False}]}
                         for ns in ("alpha", "beta")],
                "messages": [{"role": "user", "content": "find it"}]}

    def test_same_leaf_has_distinct_stable_names_and_exact_schema(self):
        import copy
        body = self.body()
        saved = copy.deepcopy(body)
        projection = S.ClientToolProjection(body)
        aliases = list(projection.names)
        self.assertEqual(len(aliases), 2)
        self.assertNotEqual(*aliases)
        self.assertTrue(all(len(a) <= 64 for a in aliases))
        self.assertEqual(projection.reverse, S.ClientToolProjection(body).reverse)
        self.assertEqual(body, saved)
        self.assertEqual(projection.body["tools"][0]["function"]["parameters"],
                         body["tools"][0]["tools"][0]["parameters"])
        self.assertIs(projection.body["tools"][0]["function"]["strict"], False)

    def test_explicit_choice_and_history_preserve_namespace_and_call_id(self):
        body = self.body()
        body["tool_choice"] = {"type": "function", "function": {"name": "search", "namespace": "beta"}}
        body["messages"] += [
            {"role": "assistant", "tool_calls": [{"id": "call_a", "type": "function",
                "namespace": "alpha", "function": {"name": "search", "arguments": '{"q":"a"}'}}]},
            {"role": "tool", "tool_call_id": "call_a", "content": "result"}]
        p = S.ClientToolProjection(body)
        self.assertEqual(p.body["tool_choice"]["function"]["name"], p.reverse[("beta", "search")])
        call = p.body["messages"][1]["tool_calls"][0]
        self.assertEqual(call["function"]["name"], p.reverse[("alpha", "search")])
        self.assertNotIn("namespace", call)
        self.assertEqual(p.restore_call(call), body["messages"][1]["tool_calls"][0])
        self.assertEqual(p.body["messages"][2], body["messages"][2])

    def test_unknown_or_ambiguous_choice_is_rejected(self):
        for choice in ({"type": "function", "name": "search"},
                       {"type": "function", "name": "search", "namespace": "missing"},
                       {"type": "namespace", "name": "alpha"}):
            body = self.body()
            body["tool_choice"] = choice
            with self.assertRaises(S.ClientToolSchemaError):
                S.ClientToolProjection(body)

    def test_qualified_choice_resolves_exactly_and_allowed_tools_is_preserved(self):
        body = self.body()
        body["tool_choice"] = {"type": "allowed_tools", "allowed_tools": {"mode": "required",
            "tools": [{"type": "function", "function": {"name": "beta.search"}}]}}
        p = S.ClientToolProjection(body)
        self.assertEqual(p.body["tool_choice"]["allowed_tools"]["tools"][0]["function"]["name"],
                         p.reverse[("beta", "search")])

    def test_invalid_schema_and_unsupported_features_fail_before_projection(self):
        for parameters in ("bad", {"type": "wat"}, {"type": "object", "properties": []},
                           {"type": "object", "required": ["undeclared"]}):
            body = self.body()
            body["tools"][0]["tools"][0]["parameters"] = parameters
            with self.assertRaises(S.ClientToolSchemaError):
                S.ClientToolProjection(body)
        body = self.body()
        body["tools"][0]["tools"][0]["defer_loading"] = True
        with self.assertRaises(S.ClientToolSchemaError):
            S.ClientToolProjection(body)

    def test_unknown_backend_function_never_becomes_a_client_call(self):
        p = S.ClientToolProjection(self.body())
        with self.assertRaises(S.ClientToolResponseError):
            p.restore_call({"id": "bad", "type": "function",
                            "function": {"name": "unknown", "arguments": "{}"}})

    def test_fragmented_interleaved_stream_names_map_without_crossing_namespaces(self):
        p = S.ClientToolProjection(self.body())
        aliases = list(p.names)
        first = {"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": i, "id": f"call_{i}", "type": "function",
             "function": {"name": name[:20], "arguments": ""}} for i, name in enumerate(aliases)]},
                              "finish_reason": None}]}
        mapped = p.restore_chunk(first)
        self.assertNotIn("tool_calls", mapped["choices"][0]["delta"])
        second = {"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": i, "function": {"name": name[20:], "arguments": "{}"}}
            for i, name in enumerate(aliases)]}, "finish_reason": None}]}
        calls = p.restore_chunk(second)["choices"][0]["delta"]["tool_calls"]
        self.assertEqual([c["namespace"] for c in calls], ["alpha", "beta"])
        self.assertEqual([c["function"]["name"] for c in calls], ["search", "search"])
        self.assertEqual([c["id"] for c in calls], ["call_0", "call_1"])

if __name__ == "__main__":
    unittest.main(verbosity=2)
