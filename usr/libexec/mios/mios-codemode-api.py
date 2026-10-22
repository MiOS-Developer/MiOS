# AI-hint: mios_tools -- the in-sandbox Code Mode tool API (WS-2). This module is the LOCAL Python API the model's generated code imports INSIDE the coderun-sandbox.
# AI-doc: usr/share/doc/mios/manual/mios.md
"""mios_tools -- the in-sandbox Code Mode tool API (WS-2).

This module is the LOCAL Python API the model's generated code imports INSIDE the
coderun-sandbox. It is the whole point of Code Mode: instead of loading ~71
OpenAI function schemas into the model's context every turn, the model writes
ordinary Python that calls e.g.

    import mios_tools
    hits = mios_tools.web_search("local FOSS LLM serving 2026")
    print(mios_tools.json({"top": hits[:3]}))      # final line = filtered result

and only the FILTERED result returns to the model -- the big token win.

How a tool call leaves the jail
-------------------------------
The sandbox is Network=none + DropCapability=ALL, so the ONLY egress is the unix
socket the Quadlet already bind-mounts at /run/coderun.sock (see
mios-coderun-sandbox@.container). This shim sends a single newline-delimited JSON
request -- {"verb": "<name>", "args": {...}} -- over that socket and reads one
JSON response line back. The HOST side (the agent-pipe's Code Mode broker proxy)
listens on that socket, runs the verb through dispatch_mios_verb (so the broker's
permission / taint-firewall / dedup / HITL gates STILL apply per verb), and
writes the result back. There is NO direct verb execution inside the jail --
every call is mediated + policy-checked on the host.

Deploy: this file is mounted into the sandbox (read-only) as
/usr/local/lib/mios/mios_tools.py and put on PYTHONPATH so `import mios_tools`
resolves. Pure stdlib (socket + json) so it has no in-sandbox deps.
"""

from __future__ import annotations

import json as _json
import os
import socket
from typing import Any

SOCKET_PATH = os.environ.get("MIOS_CODE_MODE_SOCKET", "/run/coderun.sock")
CALL_TIMEOUT_S = float(os.environ.get("MIOS_CODE_MODE_CALL_TIMEOUT_S", "60") or 60)

class ToolError(RuntimeError):
    """Raised when a tool call cannot be completed (socket down, host refusal,
    verb error). The model's code can try/except this and adapt."""

def _rpc(verb: str, args: dict) -> Any:
    """One newline-delimited JSON request -> one JSON response over the mounted
    unix socket. Raises ToolError on any transport/host failure."""
    req = _json.dumps({"verb": verb, "args": args or {}},
                     ensure_ascii=False) + "\n"
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(CALL_TIMEOUT_S)
        s.connect(SOCKET_PATH)
    except OSError as e:
        raise ToolError(f"tool socket unavailable ({SOCKET_PATH}): {e}") from e
    try:
        s.sendall(req.encode("utf-8"))
        try:
            s.shutdown(socket.SHUT_WR)
        except OSError:
            pass
        chunks = []
        while True:
            try:
                b = s.recv(65536)
            except socket.timeout as e:
                raise ToolError(f"tool call timed out: {verb}") from e
            if not b:
                break
            chunks.append(b)
    finally:
        s.close()
    raw = b"".join(chunks).decode("utf-8", "replace").strip()
    if not raw:
        raise ToolError(f"empty response for {verb}")
    line = [ln for ln in raw.splitlines() if ln.strip()][-1]
    try:
        resp = _json.loads(line)
    except ValueError as e:
        raise ToolError(f"bad response for {verb}: {raw[:200]}") from e
    if isinstance(resp, dict) and resp.get("error"):
        raise ToolError(str(resp["error"]))
    if isinstance(resp, dict) and "result" in resp:
        return resp["result"]
    return resp

def call(verb: str, **args) -> Any:
    """Generic escape hatch: call ANY MiOS verb by name. The convenience wrappers
    below are thin sugar over this. Example: mios_tools.call('web_search',
    query='...', limit=5)."""
    return _rpc(verb, args)

def web_search(query: str, limit: int = 5) -> Any:
    """Live web search via the MiOS web_search verb (SearXNG-backed)."""
    return _rpc("web_search", {"query": query, "limit": limit})

def web_scrape(url: str) -> Any:
    """Fetch + extract a URL to text/markdown via web_scrape."""
    return _rpc("web_scrape", {"url": url})

def system_status() -> Any:
    """Live system status (read)."""
    return _rpc("system_status", {})

def recall(scope: str = "global", limit: int = 30) -> Any:
    """Read durable agent memory (recall verb)."""
    return _rpc("recall", {"scope": scope, "limit": limit})

def json(obj: Any) -> str:
    """Serialise the snippet's FINAL filtered result. The Code Mode convention is
    to print this as the last stdout line so the host surfaces it under
    `result`. Equivalent to json.dumps(obj, ensure_ascii=False)."""
    return _json.dumps(obj, ensure_ascii=False)
