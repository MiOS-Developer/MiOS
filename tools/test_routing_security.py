#!/usr/bin/env python3
# AI-hint: Execute actual relay handler bodies with faulting clients to prove exception details remain server-side while success responses remain intact; pin the model router's advertised context to SSOT [routing].router_ctx.
# AI-related: usr/lib/mios/agent-pipe/mios_pipe/routing/chat.py, usr/lib/mios/agent-pipe/mios_pipe/routing/vision.py, usr/libexec/mios/mios-model-router, usr/share/mios/mios.toml
import ast
import asyncio
import json
import logging
import sys
import tomllib
import types
import unittest
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parent.parent
CANARY = 'private-internal-path-and-credential-canary'

class Response:
    def __init__(self, content, status_code=200, **kwargs):
        self.content, self.status_code = content, status_code

class Client:
    def __init__(self, failure):
        self.failure = failure
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        return False
    async def post(self, *args, **kwargs):
        if self.failure:
            raise RuntimeError(CANARY)
        return types.SimpleNamespace(status_code=200, content=b'{"ok":true}', json=lambda: {
            'choices': [{'message': {'content': 'public response'}}], 'usage': {'total_tokens': 1}})

class Request:
    headers = {}

    async def body(self):
        return b'{"model":"mios-orchestrator","input":"hello"}'


def handler(path, name, env):
    tree = ast.parse((ROOT / path).read_text(encoding='utf-8'))
    function = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == name)
    prefix = ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)
    code = ast.fix_missing_locations(ast.Module(body=[prefix, function], type_ignores=[]))
    exec(compile(code, str(path), 'exec'), env)
    return env[name]

class TestRelayErrors(unittest.TestCase):
    def test_all_three_handlers_redact_exceptions_and_preserve_success(self):
        async def run(failure):
            client = Client(failure)
            async def get_client():
                return client
            common = dict(json=json, log=Mock(spec=logging.Logger), JSONResponse=Response,
                          Response=Response, _client=client)
            chat = handler('usr/lib/mios/agent-pipe/mios_pipe/routing/chat.py', 'responses_api_logic',
                dict(common, _loads_lenient=json.loads, BACKEND_MODEL='mios-orchestrator',
                     os=types.SimpleNamespace(environ={}), uuid=__import__('uuid'), time=__import__('time'),
                     httpx=types.SimpleNamespace(AsyncClient=lambda **kw: client), _normalize_usage=lambda x:x))
            vision = handler('usr/lib/mios/agent-pipe/mios_pipe/routing/vision.py', '_client_tools_relay',
                dict(common, _tool_ctx=lambda:4096, _TOOL_BACKEND_MODEL='mios-orchestrator',
                     _TOOL_BACKEND='http://localhost/v1', _BACKEND_KEY='', _AUTH_HOSTPORTS=set(),
                     _prune_request_to_context_budget=lambda body, **kw:body, _safe_get_client=get_client))
            router = handler('usr/libexec/mios/mios-model-router', '_passthrough',
                dict(common, _cool=Mock(), _candidates=lambda model:[{
                    'name':'local', 'model':'mios-orchestrator', 'base':'http://localhost/v1'}]))
            return [await chat(Request()), await vision({}, False), await router('/chat/completions', Request())]
        for failure in (False, True):
            responses = asyncio.run(run(failure))
            self.assertEqual([r.status_code for r in responses], [502,502,503] if failure else [200]*3)
            for response in responses:
                self.assertNotIn(CANARY, str(response.content))
                if failure:
                    self.assertIn('unavailable', response.content['error']['message'])


def function(path, name):
    """One top-level def from a service file, executed alone (no service imports)."""
    tree = ast.parse((ROOT / path).read_text(encoding='utf-8'))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    env = {}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])), str(path), 'exec'), env)
    return env[name]


class TestAdvertisedContext(unittest.TestCase):
    """[routing].router_ctx reaches the router by its canonical name, and the old name still works."""

    def setUp(self):
        self.ctx = function('usr/libexec/mios/mios-model-router', '_advertised_context')
        with open(ROOT / 'usr/share/mios/mios.toml', 'rb') as fh:
            self.ssot = tomllib.load(fh)['routing']['router_ctx']

    def test_the_resolver_emits_the_canonical_and_the_derived_name(self):
        sys.path.insert(0, str(ROOT / 'usr/lib/mios'))
        import mios_toml
        exports = mios_toml.emit_exports(mios_toml.vendor_tree(str(ROOT)))
        self.assertEqual(mios_toml.canonical_name('routing.router_ctx'), 'MIOS_ROUTING_ROUTER_CTX')
        self.assertEqual(exports['MIOS_ROUTING_ROUTER_CTX'], str(self.ssot))
        self.assertEqual(exports['MIOS_ROUTER_CTX'], str(self.ssot))

    def test_a_bare_run_advertises_the_vendor_ssot_value(self):
        self.assertEqual(self.ctx({}), self.ssot)

    def test_the_canonical_name_wins_over_the_derived_alias(self):
        self.assertEqual(self.ctx({'MIOS_ROUTING_ROUTER_CTX': '65536', 'MIOS_ROUTER_CTX': '8192'}), 65536)

    def test_the_old_name_still_reaches_the_router(self):
        self.assertEqual(self.ctx({'MIOS_ROUTER_CTX': '32768'}), 32768)

    def test_an_unusable_value_falls_back_instead_of_crashing(self):
        for bad in ('', '0', '-5', 'many', '1.5', ' '):
            self.assertEqual(self.ctx({'MIOS_ROUTING_ROUTER_CTX': bad}), self.ssot, bad)


if __name__ == '__main__':
    unittest.main()
