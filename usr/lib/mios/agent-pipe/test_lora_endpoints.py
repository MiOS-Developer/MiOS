#!/usr/bin/env python3
# AI-hint: Standalone assert-script unit test for LoRA list/load endpoints (CONV-06): enabled only when [ai].heavy_engine = vllm.
# AI-related: ./server.py

import os
import sys
import types
import json
import asyncio
import unittest
from unittest import mock

here = os.path.dirname(os.path.abspath(__file__))
repo = os.path.abspath(os.path.join(here, "..", "..", "..", ".."))
toml = os.path.join(repo, "usr", "share", "mios", "mios.toml")
if "MIOS_TOML" not in os.environ and os.path.isfile(toml):
    os.environ["MIOS_TOML"] = toml

for name in ("websockets", "uvicorn"):
    sys.modules.setdefault(name, mock.MagicMock(name=name))

try:
    import httpx
except ImportError:
    MockHTTPError = type("MockHTTPError", (Exception,), {})
    httpx_mock = mock.MagicMock(name="httpx")
    httpx_mock.HTTPError = MockHTTPError
    sys.modules["httpx"] = httpx_mock

fastapi = types.ModuleType("fastapi")
class _App:
    def __getattr__(self, _attr):
        def _decorator_factory(*_a, **_k):
            def _wrap(fn=None):
                return fn if fn is not None else (lambda f: f)
            return _wrap
        return _decorator_factory
    def include_router(self, *a, **k): pass

fastapi.FastAPI = lambda *a, **k: _App()
fastapi.APIRouter = lambda *a, **k: _App()
fastapi.Request = mock.MagicMock()
fastapi.WebSocket = object

responses = types.ModuleType("fastapi.responses")
class MockJSONResponse:
    def __init__(self, content, status_code=200):
        self.content = content
        self.status_code = status_code
        if isinstance(content, (bytes, bytearray)):
            self.body = bytes(content)
        else:
            self.body = json.dumps(content).encode("utf-8")
    def json(self):
        return self.content

class MockResponse:
    def __init__(self, content, status_code=200, media_type=None):
        self.content = content
        self.status_code = status_code
        self.headers = {"content-type": media_type}
    def json(self):
        import json
        return json.loads(self.content)

setattr(responses, "JSONResponse", MockJSONResponse)
setattr(responses, "Response", MockResponse)
setattr(responses, "HTMLResponse", object)
setattr(responses, "RedirectResponse", object)
setattr(responses, "StreamingResponse", object)

sys.modules["fastapi"] = fastapi
sys.modules["fastapi.responses"] = responses

try:
    import server
except ImportError as e:
    raise unittest.SkipTest(f"missing dependencies ({e})") from e

_fails = 0

def check(name, cond, detail=""):
    global _fails
    if not cond:
        _fails += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

class MockRequest:
    def __init__(self, body_dict):
        self.body_dict = body_dict
    async def json(self):
        return self.body_dict

class MockHttpxResponse:
    def __init__(self, json_data, status_code=200):
        self.json_data = json_data
        self.status_code = status_code
        self.content = bytes(str(json_data), "utf-8")
        self.headers = {"content-type": "application/json"}
    def json(self):
        return self.json_data

def _engine(name):
    """Select the heavy lane's engine the way the SSOT does: [ai].heavy_engine."""
    real = server._toml_section
    return mock.patch("server._toml_section",
                      lambda section: {"heavy_engine": name} if section == "ai" else real(section))

async def test_lora_list_sglang_engine():
    with _engine("sglang"):
        res = await server.lora_list()
    check("lora_list (sglang): empty adapters returned", res.get("adapters") == [])
    check("lora_list (sglang): disabled is False", res.get("enabled") is False)

async def test_lora_list_vllm_engine():
    mock_models = {
        "data": [
            {"id": "base-model", "object": "model"},
            {"id": "adapter-coding", "object": "model", "parent": "base-model"},
            {"id": "adapter-reasoning", "object": "model", "root": "base-model"}
        ]
    }

    mock_client = mock.AsyncMock()
    mock_client.get.return_value = MockHttpxResponse(mock_models)

    with _engine("vllm"), mock.patch("server._get_client", mock.AsyncMock(return_value=mock_client)):
        res = await server.lora_list()
        check("lora_list (vllm): enabled is True", res.get("enabled") is True)
        adapters = res.get("adapters") or []
        check("lora_list (vllm): parsed 2 adapters", len(adapters) == 2)
        check("lora_list (vllm): coding adapter present", any(a["id"] == "adapter-coding" for a in adapters))

async def test_lora_load_sglang_engine():
    req = MockRequest({"lora_name": "coding", "lora_path": "/path"})
    with _engine("sglang"):
        res = await server.lora_load(req)
    check("lora_load (sglang): status code is 400", res.status_code == 400)
    check("lora_load (sglang): returns error message", "only supported" in res.content.get("error"))

async def test_lora_load_vllm_engine():
    req = MockRequest({"lora_name": "coding", "lora_path": "/path"})

    mock_client = mock.AsyncMock()
    mock_client.post.return_value = MockHttpxResponse({"status": "loaded"})

    with _engine("vllm"), mock.patch("server._get_client", mock.AsyncMock(return_value=mock_client)):
        res = await server.lora_load(req)
        check("lora_load (vllm): status code is 200", res.status_code == 200)

async def main():
    await test_lora_list_sglang_engine()
    await test_lora_list_vllm_engine()
    await test_lora_load_sglang_engine()
    await test_lora_load_vllm_engine()

    if _fails > 0:
        sys.exit(1)
    else:
        sys.exit(0)

if __name__ == "__main__":
    asyncio.run(main())
