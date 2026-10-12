# AI-hint: Shared layered SSOT endpoint and fail-closed HTTP verdict contract for Windows OS-control clients.
# AI-related: mios-pc-control, mios-oscontrol-health, mios-windows, mios_toml
"""Transport success is not evidence that a desktop action succeeded."""

import json
import os
import sys
import urllib.parse
import urllib.request

import mios_toml


def executor_endpoint():
    """Use the layered endpoint or derived service port; never substitute a router."""
    cfg = mios_toml.load_merged()
    endpoint = os.environ.get("MIOS_OSCONTROL_EXECUTOR") or cfg.get(
        "os_control", {}).get("executor_endpoint", "")
    if not endpoint:
        port = cfg.get("ports", {}).get("oscontrol")
        if type(port) is not int or not 0 < port < 65536:
            raise ValueError("SSOT ports.oscontrol must be a valid TCP port")
        endpoint = f"http://127.0.0.1:{port}"
    if not isinstance(endpoint, str):
        raise ValueError("OS-control executor endpoint must be a URL")
    endpoint = endpoint.strip().rstrip("/")
    if any(ord(char) < 33 or char in '"\\' for char in endpoint):
        raise ValueError("OS-control endpoint contains invalid URL characters")
    parsed = urllib.parse.urlsplit(endpoint)
    if (parsed.scheme not in ("http", "https") or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment):
        raise ValueError("OS-control endpoint must be an HTTP URL without credentials, query or fragment")
    # Accessing port rejects malformed/out-of-range explicit URL ports.
    if parsed.port == 0:
        raise ValueError("OS-control endpoint port must be a valid TCP port")
    return endpoint


def require_verdict(response, required=None):
    """Require literal positive booleans and reject contradictory success fields."""
    if not isinstance(response, dict):
        raise ValueError("executor response must be a JSON object")
    verdicts = ("ok", "verified", "launched", "success")
    present = [key for key in verdicts if key in response]
    if not present:
        raise ValueError("executor response has no success verdict")
    if required is not None and response.get(required) is not True:
        raise ValueError(f"executor did not confirm {required}")
    for key in present:
        if response[key] is not True:
            raise ValueError(f"executor verdict {key} is not literal true")
    if "fired" in response and response["fired"] is not True:
        raise ValueError("executor fired verdict contradicts success")
    if response.get("error"):
        raise ValueError("executor returned an error with a success verdict")
    nested = response.get("verdict")
    if nested is not None:
        if not isinstance(nested, dict):
            raise ValueError("executor nested verdict must be a JSON object")
        for key in verdicts:
            if key in nested and nested[key] is not True:
                raise ValueError(f"executor nested verdict {key} contradicts success")
    return response


def request_json(url, method="GET", data=None, timeout=15, required=None):
    payload = None if data is None else json.dumps(data).encode("utf-8")
    headers = {} if payload is None else {"Content-Type": "application/json"}
    req = urllib.request.Request(url, data=payload, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        if not 200 <= response.status < 300:
            raise ValueError(f"executor HTTP status {response.status}")
        body = json.loads(response.read().decode("utf-8"))
    return require_verdict(body, required)


def main():
    if len(sys.argv) == 2 and sys.argv[1] == "endpoint":
        print(executor_endpoint())
    elif len(sys.argv) == 2 and sys.argv[1] == "health":
        request_json(executor_endpoint() + "/health", timeout=3, required="ok")
    else:
        raise ValueError("usage: mios_oscontrol_client.py endpoint|health")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Do not echo remote error bodies, URLs with credentials or request payloads.
        print(f"OS-control contract failed: {type(error).__name__}", file=sys.stderr)
        sys.exit(1)
