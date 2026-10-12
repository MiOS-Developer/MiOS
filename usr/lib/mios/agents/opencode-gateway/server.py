#!/usr/bin/env python3
# AI-hint: Provides an OpenAI-compatible HTTP shim for the opencode CLI, exposing /v1/models and /v1/chat/completions endpoints to in...
# AI-doc: usr/share/doc/mios/manual/opencode-gateway.md
"""
MiOS opencode → OpenAI /v1 gateway shim.

opencode (the SST/charm CLI coding agent) speaks its own CLI protocol, not the
OpenAI /v1 chat-completions contract that the MiOS agent-pipe council expects.
This shim wraps `opencode run` behind a minimal OpenAI-compatible HTTP server so
opencode can be dispatched as a first-class /v1 council peer (like Hermes at
:8720), without teaching agent-pipe a bespoke protocol.

Endpoints:
  GET  /v1/models            → advertise the single opencode model id
  POST /v1/chat/completions  → run opencode, return an OpenAI chat.completion
                               (or an SSE delta stream when stream=true)

Config (all via env, SSOT-rendered by the unit / userenv.sh):
  MIOS_PORTS_OPENCODE_GATEWAY   listen port (default 8780)
  MIOS_OPENCODE_BIN            path to the opencode binary
  MIOS_OPENCODE_MODEL          model id to advertise/forward (ONE canonical id;
                               must match [agents.opencode].model + the key in
                               opencode.json)
  MIOS_OPENCODE_PROVIDER       opencode provider name from opencode.json
                               (default "local"); used to build the `-m
                               provider/model` selector
  MIOS_OPENCODE_CONFIG         explicit path to opencode.json; exported to the
                               child as OPENCODE_CONFIG so opencode does NOT
                               depend on a hardcoded /root/.config location
  MIOS_OPENCODE_HOST           bind host (default 127.0.0.1)
  MIOS_OPENCODE_TIMEOUT_S      per-run timeout seconds (default 90; SSOT key
                               [ai].opencode_gateway_timeout_s). Legacy
                               MIOS_OPENCODE_TIMEOUT is still honoured as a
                               fallback for older overlays.
"""
import os
import sys
import json
import time
import uuid
import subprocess

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = os.environ.get("MIOS_OPENCODE_HOST", "127.0.0.1")
PORT = int(os.environ.get("MIOS_PORTS_OPENCODE_GATEWAY", "8780"))
OPENCODE_BIN = os.environ.get(
    "MIOS_OPENCODE_BIN", "/usr/lib/mios/agents/opencode/bin/opencode"
)
OPENCODE_MODEL = os.environ.get("MIOS_OPENCODE_MODEL", "mios-opencode:latest")
ADVERTISED_MODEL = os.environ.get("MIOS_AI_GATEWAY_MODEL", "MiOS AI")
OPENCODE_PROVIDER = os.environ.get("MIOS_OPENCODE_PROVIDER", "local")
OPENCODE_CONFIG = os.environ.get(
    "MIOS_OPENCODE_CONFIG", "/etc/mios/opencode/opencode.json"
)
TIMEOUT = int(
    os.environ.get("MIOS_OPENCODE_TIMEOUT_S")
    or os.environ.get("MIOS_OPENCODE_TIMEOUT")
    or "90"
)

def _selector(model: str) -> str:
    """Return the configured selector; requests cannot change providers."""
    canonical = (OPENCODE_MODEL if "/" in OPENCODE_MODEL
                 else f"{OPENCODE_PROVIDER}/{OPENCODE_MODEL}")
    selected = model if "/" in model else f"{OPENCODE_PROVIDER}/{model}"
    for value in (canonical, selected):
        if value.startswith("-") or any(char in value for char in "\0\r\n"):
            raise ValueError("invalid opencode model selector")
    if selected != canonical:
        raise ValueError("opencode model selector is not configured")
    return canonical

def _flatten_messages(messages):
    """Collapse an OpenAI messages array into a single opencode `run` prompt.

    opencode's non-interactive `run` takes one prompt string, so we serialise
    the whole conversation (system + history + latest user turn) into a clearly
    delimited transcript instead of dropping everything but the last user line.
    Returns (system_prompt, prompt_text).
    """
    system_parts = []
    convo = []
    for m in messages or []:
        role = (m.get("role") or "user").lower()
        content = m.get("content", "")
        if isinstance(content, list):
            text = "".join(
                part.get("text", "")
                for part in content
                if isinstance(part, dict)
            )
        else:
            text = str(content)
        if not text.strip():
            continue
        if role == "system":
            system_parts.append(text)
        elif role == "assistant":
            convo.append(f"Assistant:\n{text}")
        elif role == "tool":
            convo.append(f"Tool result:\n{text}")
        else:  # user (and any unknown role)
            convo.append(f"User:\n{text}")

    system_prompt = "\n\n".join(system_parts).strip()
    transcript = "\n\n".join(convo).strip()

    if system_prompt:
        prompt = (
            f"# System instructions\n{system_prompt}\n\n"
            f"# Conversation\n{transcript}\n\n"
            f"# Task\nRespond as the assistant to the latest user turn above."
        )
    else:
        prompt = transcript
    return system_prompt, prompt

def _run_opencode(prompt: str, model: str):
    """Invoke `opencode run` with the unified config + model selector.

    Returns the assistant text. Raises on failure.

    interactive TUI (spinner/replay frames interleaved with the answer) which
    (a) pollutes the returned text with control noise and (b) wedges when stdout
    is a partial / early-closed consumer -- the long-standing "opencode run
    hangs / returns zero" symptom that made this peer inert (
    opencode default=false/fanout=false). `--format json` emits ONE JSON event
    per line (step_start / text / step_finish) with no TUI, so the process exits
    promptly at step_finish and we extract exactly the assistant text. Verified
    on opencode 1.17.7: a default-format run wedged behind a truncating pipe;
    --format json completed cleanly (step_finish reason=stop) every time.
    """
    env = dict(os.environ)
    env["OPENCODE_CONFIG"] = OPENCODE_CONFIG
    cfg_dir = os.path.dirname(os.path.dirname(OPENCODE_CONFIG))
    if cfg_dir:
        env.setdefault("XDG_CONFIG_HOME", cfg_dir)

    # Upstream 1.17.7 run.ts reads Bun.stdin.text() for non-TTY input. Keep
    # request text out of argv, including option-like and multiline prompts.
    cmd = [OPENCODE_BIN, "run", "--format", "json", "-m", _selector(model)]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", timeout=TIMEOUT, env=env,
        input=prompt,
    )
    raw = proc.stdout or ""
    texts = []
    parsed_any = False
    for ln in raw.splitlines():
        ln = ln.strip()
        if not ln or ln[0] != "{":
            continue
        try:
            ev = json.loads(ln)
        except Exception:
            continue
        parsed_any = True
        part = ev.get("part") or {}
        if part.get("type") == "text" and part.get("text"):
            texts.append(part["text"])
        else:
            st = part.get("state")
            if isinstance(st, dict):
                for k in ("output", "text", "result", "stdout"):
                    v = st.get(k)
                    if isinstance(v, str) and v.strip():
                        texts.append(v)
                        break
    out = "".join(texts).strip()
    if not out and not parsed_any:
        out = raw.strip() or (proc.stderr or "").strip()
    if proc.returncode != 0 and not out:
        raise RuntimeError(
            f"opencode exited {proc.returncode} with no output"
        )
    return out

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def _send(self, code, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _sse_headers(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

    def _sse_write(self, obj):
        self.wfile.write(b"data: " + json.dumps(obj).encode("utf-8") + b"\n\n")
        self.wfile.flush()

    def do_GET(self):
        if self.path.rstrip("/") == "/v1/models":
            self._send(200, {
                "object": "list",
                "data": [
                    {"id": ADVERTISED_MODEL, "object": "model", "owned_by": "mios"}
                ],
            })
        elif self.path.rstrip("/") in ("/health", "/healthz"):
            self._send(200, {"status": "ok", "model": ADVERTISED_MODEL})
        else:
            self._send(404, {"error": {"message": "not found"}})

    def do_POST(self):
        if self.path.rstrip("/") != "/v1/chat/completions":
            self._send(404, {"error": {"message": "not found"}})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            req = json.loads(raw or b"{}")
        except Exception as e:
            self._send(400, {"error": {"message": f"bad request: {e}"}})
            return

        messages = req.get("messages", [])
        _req_model = str(req.get("model") or "")
        run_model = _req_model if "/" in _req_model else OPENCODE_MODEL
        try:
            _selector(run_model)
        except ValueError:
            self._send(400, {"error": {
                "message": "Requested model is not configured",
                "type": "invalid_request_error", "code": "model_not_configured",
            }})
            return
        model = ADVERTISED_MODEL
        stream = bool(req.get("stream", False))

        _system, prompt = _flatten_messages(messages)

        cmpl_id = "chatcmpl-" + uuid.uuid4().hex[:24]
        created = int(time.time())

        if stream:
            self._stream(cmpl_id, created, model, prompt, run_model)
            return

        try:
            out = _run_opencode(prompt, run_model)
        except Exception as e:
            self._send(500, {"error": {"message": f"opencode failed: {e}"}})
            return

        self._send(200, {
            "id": cmpl_id,
            "object": "chat.completion",
            "created": created,
            "model": model,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": out},
                "finish_reason": "stop",
            }],
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            },
        })

    def _stream(self, cmpl_id, created, model, prompt, run_model=None):
        """Emit a well-formed OpenAI SSE delta stream.

        opencode's `run` is not token-incremental over a stable public API, so
        we run it to completion then chunk the result into SSE deltas. This
        keeps stream=true callers (agent-pipe council) happy with a valid
        chat.completion.chunk stream terminated by [DONE]. `model` is the SURFACE
        id echoed in every chunk ("MiOS AI"); `run_model` is the internal opencode
        subprocess selector (defaults to `model` for back-compat callers).
        """
        try:
            out = _run_opencode(prompt, run_model or model)
            err = None
        except Exception as e:
            out, err = "", str(e)

        self._sse_headers()

        self._sse_write({
            "id": cmpl_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{
                "index": 0,
                "delta": {"role": "assistant"},
                "finish_reason": None,
            }],
        })

        if err:
            self._sse_write({
                "id": cmpl_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{
                    "index": 0,
                    "delta": {"content": f"opencode failed: {err}"},
                    "finish_reason": None,
                }],
            })
        else:
            step = 512
            for i in range(0, len(out), step):
                self._sse_write({
                    "id": cmpl_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [{
                        "index": 0,
                        "delta": {"content": out[i:i + step]},
                        "finish_reason": None,
                    }],
                })

        self._sse_write({
            "id": cmpl_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{
                "index": 0,
                "delta": {},
                "finish_reason": "stop",
            }],
        })
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

def main():
    addr = (HOST, PORT)
    httpd = ThreadingHTTPServer(addr, Handler)
    sys.stderr.write(
        f"[opencode-gateway] listening on http://{HOST}:{PORT}/v1 "
        f"(model={OPENCODE_MODEL}, bin={OPENCODE_BIN})\n"
    )
    httpd.serve_forever()

if __name__ == "__main__":
    main()
