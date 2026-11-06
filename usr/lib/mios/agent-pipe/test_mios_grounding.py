#!/usr/bin/env python3
# AI-hint: Standalone assert-script unit test for mios_grounding (refactor R2 leaf extraction of the per-turn ENV-GROUNDING cluster)....
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md
"""Unit tests for mios_grounding (refactor R2)."""

import logging
import os
import sys

import mios_grounding as g

_fails = 0

def check(name, cond, detail=""):
    global _fails
    if not cond:
        _fails += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

class FakeVar:
    """Minimal ContextVar stand-in (only .get() is used by the cluster)."""

    def __init__(self, value):
        self._v = value

    def get(self):
        return self._v

def t_capability():
    cat = {
        "open_app": {"section": "Apps", "model_name": "open_app"},
        "close_app": {"section": "Apps", "model_name": "close_app"},
        "rare_one": {"section": "Apps", "model_name": "rare_one", "tier": "rare"},
        "web_search": {"section": "Web"},
    }
    out = g._capability_grounding(cat)
    check("capability: is str", isinstance(out, str))
    check("capability: header present", "YOUR ACTUAL CAPABILITIES" in out)
    check("capability: shows section", "Apps:" in out and "Web:" in out)
    check("capability: shows verb name", "open_app" in out and "web_search" in out)
    check("capability: omits rare-tier verb", "rare_one" not in out)
    check("capability: empty catalog -> ''", g._capability_grounding({}) == "")

def t_host_os():
    tz = g._host_timezone()
    check("host_timezone: returns str", isinstance(tz, str))
    osi = g._get_os_info()
    check("get_os_info: non-empty str", isinstance(osi, str) and len(osi) > 0, repr(osi))

def t_client_env():
    body = {
        "metadata": {
            "variables": {
                "{{USER_LOCATION}}": "Paris, France",
                "{{CURRENT_TIMEZONE}}": "Europe/Paris",
                "{{USER_LANGUAGE}}": "fr-FR",
                "{{CURRENT_DATE}}": "2026-06-25",
                "{{USER_NAME}}": "Unknown",  # sentinel -> must be dropped
            }
        },
        "user": "fallback-name",
    }
    out = g._client_env(body)
    check("client_env: is dict", isinstance(out, dict))
    check("client_env: location", out.get("location") == "Paris, France")
    check("client_env: timezone", out.get("timezone") == "Europe/Paris")
    check("client_env: language", out.get("language") == "fr-FR")
    check("client_env: date", out.get("date") == "2026-06-25")
    check("client_env: sentinel dropped, user fallback", out.get("user_name") == "fallback-name")
    check("client_env: non-dict body -> {}", g._client_env(None) == {})

    body_with_coords = {
        "metadata": {
            "variables": {
                "{{USER_LOCATION}}": "New York, USA (40.7128, -74.0060)",
            }
        }
    }
    out_with_coords = g._client_env(body_with_coords)
    check("client_env: coordinates stripped", out_with_coords.get("location") == "New York, USA")

def t_env_block():
    g.configure(
        client_env_var=FakeVar({
            "surface": "cli", "host": "mios-host", "cwd": "/srv/work",
            "location": "Testville", "user_name": "Ada", "language": "en-US",
        }),
        current_date_str=lambda: "2026-06-25",
    )
    blk = g._env_block()
    check("env_block: is str", isinstance(blk, str))
    check("env_block: opens <env>", blk.lstrip().startswith("<env>"))
    check("env_block: closes </env>", blk.rstrip().endswith("</env>"))
    for k, v in (("current_date", "2026-06-25"), ("surface", "cli"),
                 ("host", "mios-host"), ("cwd", "/srv/work"),
                 ("location", "Testville"), ("location_source", "client"),
                 ("user", "Ada"), ("language", "en-US")):
        check(f"env_block: {k}:{v}", f"{k}: {v}" in blk, blk)
    g.configure(client_env_var=FakeVar({"surface": "cli"}))
    blk2 = g._env_block()
    check("env_block: unknown location is explicit", "location: UNKNOWN" in blk2, blk2)

def t_env_grounding():
    g.configure(
        client_env_var=FakeVar({"surface": "owui", "location": "Testville"}),
        current_date_str=lambda: "2026-06-25",
    )
    out = g._env_grounding()
    check("env_grounding: is str", isinstance(out, str))
    check("env_grounding: includes <env> block", "<env>" in out)
    check("env_grounding: identity guard (local)", "LOCAL" in out and "Identity" in out)
    check("env_grounding: self-architecture", "MiOS" in out)
    check("env_grounding: temporal grounding", "Temporal grounding" in out)
    check("env_grounding: anti-stale warning", "stale" in out.lower())

class _Records(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)

def _bind_turn(mode):
    """One _client_env turn under bind mode `mode`: the body claims 'claimed', the
    caller-key's bound account is 'bound'. Returns (user_name, log records)."""
    rec = _Records()
    logger = logging.getLogger("mios-agent-pipe")
    logger.addHandler(rec)
    prior = os.environ.get("MIOS_PRINCIPAL_BIND_MODE")
    os.environ["MIOS_PRINCIPAL_BIND_MODE"] = mode
    g.configure(check_inbound_principal=lambda tok: {"account": "bound"} if tok == "k1" else None)
    try:
        out = g._client_env({"user": "claimed"}, {"authorization": "Bearer k1"})
    finally:
        logger.removeHandler(rec)
        if prior is None:
            os.environ.pop("MIOS_PRINCIPAL_BIND_MODE", None)
        else:
            os.environ["MIOS_PRINCIPAL_BIND_MODE"] = prior
    return out.get("user_name"), rec.records

def t_principal_bind_mode():
    p = g._parse_principal_bind_mode

    def parsed(raw):
        try:
            return p(raw)
        except ValueError as exc:  # a rejected canonical value is a FAIL line, not a crash
            return exc

    for raw, want in (("off", "off"), ("verify", "verify"), ("enforce", "enforce"),
                      ("audit", "verify"), (" AUDIT ", "verify"), (None, "off"), ("", "off")):
        got = parsed(raw)
        check(f"bind mode: {raw!r} -> {want}", got == want, repr(got))
    # An unknown value is REJECTED with a message naming the key, the value and
    # every accepted spelling -- never silently read as off.
    for bad in ("bogus", "on", "true", ["enforce"]):
        try:
            p(bad)
            check(f"bind mode: {bad!r} is rejected", False, "accepted")
        except ValueError as exc:
            msg = str(exc)
            check(f"bind mode: {bad!r} is rejected with a clear message",
                  "[security].principal_bind_mode" in msg and repr(bad) in msg
                  and all(w in msg for w in ("off", "verify", "audit", "enforce")), msg)

    # The vendor value "audit" observes like verify: the mismatch is logged and
    # the claimed owner is kept. NEGATIVE CONTROL: off logs nothing.
    who, logs = _bind_turn("audit")
    check("bind audit: claimed owner kept", who == "claimed", repr(who))
    check("bind audit: mismatch logged as verify",
          any("mode=verify" in r.getMessage() for r in logs), [r.getMessage() for r in logs])
    who, logs = _bind_turn("off")
    check("bind off (negative control): claimed owner kept, no mismatch logged",
          who == "claimed" and not any("principal-bind" in r.getMessage() for r in logs))
    who, _ = _bind_turn("enforce")
    check("bind enforce: the token-bound account is the owner", who == "bound", repr(who))

    # An unknown value fails CLOSED to enforce, with one error naming it.
    who, logs = _bind_turn("verfy")
    errors = [r.getMessage() for r in logs if r.levelno >= logging.ERROR]
    check("bind unknown: fails closed to the token-bound owner", who == "bound", repr(who))
    check("bind unknown: one error names the rejected value",
          len(errors) == 1 and "'verfy'" in errors[0] and "fail" in errors[0], errors)
    _, logs = _bind_turn("verfy")
    check("bind unknown: the error is not repeated every turn",
          not [r for r in logs if r.levelno >= logging.ERROR])
    os.environ["MIOS_PRINCIPAL_BIND_MODE"] = "verfy"
    try:
        check("bind unknown: _principal_bind_mode reports enforce",
              g._principal_bind_mode() == "enforce")
    finally:
        os.environ.pop("MIOS_PRINCIPAL_BIND_MODE", None)

def main():
    t_capability()
    t_host_os()
    t_client_env()
    t_env_block()
    t_env_grounding()
    t_principal_bind_mode()
    print(f"\n{'ALL PASS' if _fails == 0 else str(_fails) + ' FAIL(S)'}")
    sys.exit(1 if _fails else 0)

if __name__ == "__main__":
    main()
