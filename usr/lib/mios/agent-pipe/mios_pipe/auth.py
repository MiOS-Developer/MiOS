# AI-hint: Extracted module for auth.py.
"""Auth and Usage Middleware."""

from __future__ import annotations

import json
from starlette.responses import Response, JSONResponse

_loads_lenient = None
_usage_estimate = None
_council_mode_var = None
_API_REQUIRE_AUTH = False
_AUTH_OPEN_PATHS = ()
_AUTH_GATED_PREFIXES = ()
_check_inbound_principal = None

_ALLOWLIST_HOSTS: set[str] = set()

def _is_allowed_host(host_hdr: str) -> bool:
    if not host_hdr:
        return True
    h = host_hdr.split(":")[0].strip().lower()
    return h in _ALLOWLIST_HOSTS

def _is_allowed_origin(origin_hdr: str) -> bool:
    if not origin_hdr:
        return True
    from urllib.parse import urlparse
    try:
        parsed = urlparse(origin_hdr)
        h = (parsed.hostname or "").strip().lower()
        return not h or h in _ALLOWLIST_HOSTS
    except Exception:
        return False

def configure(*, loads_lenient=None, usage_estimate=None, council_mode_var=None,
              api_require_auth=None, auth_open_paths=None, auth_gated_prefixes=None,
              check_inbound_principal=None, allowlist_hosts=None):
    global _loads_lenient, _usage_estimate, _council_mode_var, _API_REQUIRE_AUTH
    global _AUTH_OPEN_PATHS, _AUTH_GATED_PREFIXES, _check_inbound_principal, _ALLOWLIST_HOSTS
    if loads_lenient is not None: _loads_lenient = loads_lenient
    if usage_estimate is not None: _usage_estimate = usage_estimate
    if council_mode_var is not None: _council_mode_var = council_mode_var
    if api_require_auth is not None: _API_REQUIRE_AUTH = api_require_auth
    if auth_open_paths is not None: _AUTH_OPEN_PATHS = auth_open_paths
    if auth_gated_prefixes is not None: _AUTH_GATED_PREFIXES = auth_gated_prefixes
    if check_inbound_principal is not None: _check_inbound_principal = check_inbound_principal
    if allowlist_hosts is not None:
        _ALLOWLIST_HOSTS = {h.strip().lower() for h in allowlist_hosts if h and str(h).strip()}

async def host_origin_validation_mw(request, call_next):
    """P1-15 (task_3562): reject a Host or Origin outside [security].allowlist_hosts (DNS rebinding / CSRF protection)."""
    if _ALLOWLIST_HOSTS:
        host = request.headers.get("host")
        if host and not _is_allowed_host(host):
            return JSONResponse(
                status_code=403,
                content={"error": {"message": f"403: Host '{host}' outside allowlist_hosts",
                                   "type": "invalid_host", "code": 403}}
            )
        origin = request.headers.get("origin")
        if origin and not _is_allowed_origin(origin):
            return JSONResponse(
                status_code=403,
                content={"error": {"message": f"403: Origin '{origin}' outside allowlist_hosts",
                                   "type": "invalid_origin", "code": 403}}
            )
    return await call_next(request)

async def usage_completeness_mw(request, call_next):
    """Tier-0 conformance: guarantee EVERY non-streaming /v1/chat/completions JSON
    response carries a `usage` object."""
    response = await call_next(request)
    if (request.url.path != "/v1/chat/completions"
            or "application/json" not in response.headers.get("content-type", "")):
        return response
    body = b""
    try:
        async for chunk in response.body_iterator:
            body += chunk
    except Exception:
        return response
    out = body
    try:
        data = _loads_lenient(body.decode("utf-8", "replace")) if _loads_lenient else None
        if isinstance(data, dict) and data.get("object") == "chat.completion":
            _changed = False
            from mios_tokenize import _normalize_usage
            _usage = data.get("usage")
            _ans = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            _normalized = _normalize_usage(_usage or (_usage_estimate("", _ans) if _usage_estimate else {}))
            if _usage != _normalized:
                data["usage"] = _normalized
                _changed = True
            if "mios_mode" not in data and _council_mode_var:
                try:
                    data["mios_mode"] = _council_mode_var.get()
                    _changed = True
                except Exception:
                    pass
            if _changed:
                out = json.dumps(data).encode()
    except Exception:
        out = body
    _hdrs = {k: v for k, v in response.headers.items() if k.lower() != "content-length"}
    return Response(content=out, status_code=response.status_code,
                    headers=_hdrs, media_type="application/json")

async def inbound_auth_mw(request, call_next):
    """FED-G1: gate /v1/* + /a2a with a credential when [security].api_require_auth is ON."""
    if not _API_REQUIRE_AUTH:
        return await call_next(request)
    path = request.url.path
    if (path in _AUTH_OPEN_PATHS
            or not any(path.startswith(p) for p in _AUTH_GATED_PREFIXES)):
        return await call_next(request)
    try:
        _tok = (request.headers.get("authorization") or "").removeprefix("Bearer ").strip()
        princ = _check_inbound_principal(_tok) if _check_inbound_principal else None
    except Exception:
        princ = None
    if princ is None:
        return JSONResponse(
            content={"error": {"message": "unauthorized: a valid credential is required",
                               "type": "invalid_request_error", "code": "unauthorized"}},
            status_code=401)
    try:
        request.state.mios_principal = princ
    except Exception:
        pass
    return await call_next(request)
