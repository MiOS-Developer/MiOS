# AI-hint: RBAC/PDP/quota + human-in-the-loop POLICY plane extracted verbatim from server.py (refactor R7 security wave).
# AI-doc: usr/share/doc/mios/manual/access.md

from __future__ import annotations

import asyncio
import contextvars  # noqa: F401 -- referenced in injected ContextVar type hints
import logging
import time
from typing import Optional

import mios_pdp as _pdp   # WS-A9 policy decision point (capability gate)
import mios_pg as _mios_pg   # WS-9 pg client -- the durable quota ledger
import mios_quota         # WS-6 per-user quota / rate-limit (inert until configured)
import mios_hitl          # the shared HITL verdict resolver (mios_hitl.decide) both gates route through
from mios_config import _toml_section   # layered mios.toml SSOT reader

log = logging.getLogger("mios-agent-pipe")

_VERB_CATALOG: dict = {}
_RECIPE_CATALOG: dict = {}
_AGENT_REGISTRY: dict = {}

_hitl_approved_var = None
_hitl_blocked_var = None
_client_env_var = None
_dispatch_agent_var = None

_pending_hash = None
_get_client = None
_db_fire = None
_db_post = None
_db_create = None

def configure(*, verb_catalog=None, recipe_catalog=None, agent_registry=None,
              hitl_approved_var=None, hitl_blocked_var=None, client_env_var=None,
              dispatch_agent_var=None, pending_hash=None, get_client=None,
              db_fire=None, db_post=None, db_create=None) -> None:
    """Inject server.py's catalogs, registry, ContextVars and runtime helpers the
    RBAC/PDP/quota/HITL policy helpers call back into. Idempotent; every arg is
    optional so a membership reload can re-inject just ``agent_registry``."""
    global _VERB_CATALOG, _RECIPE_CATALOG, _AGENT_REGISTRY
    global _hitl_approved_var, _hitl_blocked_var, _client_env_var
    global _dispatch_agent_var, _pending_hash, _get_client
    global _db_fire, _db_post, _db_create
    if verb_catalog is not None:
        _VERB_CATALOG = verb_catalog
    if recipe_catalog is not None:
        _RECIPE_CATALOG = recipe_catalog
    if agent_registry is not None:
        _AGENT_REGISTRY = agent_registry
    if hitl_approved_var is not None:
        _hitl_approved_var = hitl_approved_var
    if hitl_blocked_var is not None:
        _hitl_blocked_var = hitl_blocked_var
    if client_env_var is not None:
        _client_env_var = client_env_var
    if dispatch_agent_var is not None:
        _dispatch_agent_var = dispatch_agent_var
    if pending_hash is not None:
        globals()["_pending_hash"] = pending_hash
    if get_client is not None:
        globals()["_get_client"] = get_client
    if db_fire is not None:
        globals()["_db_fire"] = db_fire
    if db_post is not None:
        globals()["_db_post"] = db_post
    if db_create is not None:
        globals()["_db_create"] = db_create

_PERMISSION_TIERS = [
    str(t).strip().lower()
    for t in ((_toml_section("ai") or {}).get("permission_tiers")
              or ["read", "write", "interactive"])
    if str(t).strip()
] or ["read", "write", "interactive"]

def _perm_rank(perm: str) -> int:
    """Risk rank of a permission tier (lower index = safer). A tier not in the
    lattice ranks ABOVE the top (most restrictive) so an unclassified verb is
    gated rather than silently granted -- fail-closed on the risk axis."""
    p = str(perm or "").strip().lower()
    try:
        return _PERMISSION_TIERS.index(p)
    except ValueError:
        return len(_PERMISSION_TIERS)

_HITL_MODE = str((_toml_section("ai") or {}).get("hitl_mode") or "off").strip().lower()
_HITL_THRESHOLD = str((_toml_section("ai") or {}).get("hitl_threshold")
                      or "interactive").strip().lower()

def _effective_perm(tool: str, args: "Optional[dict]" = None) -> str:
    vperm = str((_VERB_CATALOG.get(tool) or {}).get("permission", "read")).lower()
    try:
        if tool == "os_recipe" and args:
            rn = str((args or {}).get("name") or "").strip().replace("_", "-")
            rc = _RECIPE_CATALOG.get(rn)
            if rc:
                return str(rc.get("permission", vperm)).lower()
    except Exception:  # noqa: BLE001 -- degrade-open
        pass
    return vperm

def _hitl_block_reason(tool: str, args: "Optional[dict]" = None) -> "Optional[str]":
    if _HITL_MODE not in ("audit", "block"):
        return None
    try:
        _appr = _hitl_approved_var.get()
        if _appr and _appr == _pending_hash(tool, args or {}):
            return None
        vperm = _effective_perm(tool, args)
        in_tier = _perm_rank(vperm) >= _perm_rank(_HITL_THRESHOLD)
        verdict = mios_hitl.decide(in_tier_scope=in_tier, ai_mode=_HITL_MODE)
        if verdict == mios_hitl.OBSERVE:
            log.info("HITL audit: %s (tier=%s >= %s) WOULD require approval -- "
                     "proceeding (audit mode)", tool, vperm, _HITL_THRESHOLD)
            return None
        if verdict != mios_hitl.BLOCK:
            return None  # below the gate threshold -> not human-gated
        log.info("HITL block: refused %s (tier=%s) pending human approval", tool, vperm)
        try:
            _bl = _hitl_blocked_var.get()
            if not isinstance(_bl, list):
                _bl = []
                _hitl_blocked_var.set(_bl)
            if tool not in _bl:
                _bl.append(tool)
        except Exception:  # noqa: BLE001
            pass
        return (f"'{tool}' is a {vperm}-tier action and HITL block-mode is ON: it "
                f"needs explicit human approval before running, so it was NOT "
                f"executed. Approve it, or set [ai].hitl_mode to audit/off, to proceed.")
    except Exception:  # noqa: BLE001 -- degrade-open: a gate bug never blocks work
        return None

_raw_arbiter_url = str((_toml_section("ai") or {}).get("hitl_arbiter_url") or "").strip()
_default_arbiter_port = int((_toml_section("ports") or {}).get("arbiter", 8760) or 8760)
_HITL_ARBITER_URL = _raw_arbiter_url if _raw_arbiter_url else f"http://127.0.0.1:{_default_arbiter_port}"
_HITL_ARBITER_FAIL = str((_toml_section("ai") or {}).get("hitl_arbiter_fail")
                         or "closed").strip().lower()

def _emit_arbiter_audit_event(kind: str, summary: str, payload: dict) -> None:
    try:
        if _db_fire and _db_post and _db_create:
            _db_fire(_db_post(_db_create("event", {
                "source": "agent-pipe",
                "kind": kind,
                "severity": "high",
                "summary": summary[:200],
                "payload": payload,
            }, now_fields=("ts",))))
    except Exception:
        pass

async def _hitl_arbiter_verdict(tool: str, args: dict) -> "Optional[str]":
    """Consult the external policy arbiter for a high-risk action; return a refusal
    reason on DENY, else None (allow/not-applicable). Wire fallback to
    http://127.0.0.1:{ports.arbiter}. Fail-closed per _HITL_ARBITER_FAIL."""
    arbiter_url = str(_HITL_ARBITER_URL or "").strip()
    if not arbiter_url:
        port = int((_toml_section("ports") or {}).get("arbiter", 8760) or 8760)
        arbiter_url = f"http://127.0.0.1:{port}"
    try:
        vperm = _effective_perm(tool, args)
        if _perm_rank(vperm) < _perm_rank(_HITL_THRESHOLD):
            return None  # below the threshold -> arbiter not consulted
        client = await _get_client()
        r = await client.post(
            arbiter_url,
            json={"verb": tool, "tier": vperm, "args": args},
            timeout=5.0)
        if r.status_code == 200:
            v = r.json() if r.content else {}
            if isinstance(v, dict) and not v.get("allow", True):
                reason = str(v.get("reason") or f"'{tool}' denied by the policy arbiter.")
                log.info("HITL arbiter: DENY %s (tier=%s): %s",
                         tool, vperm, reason)
                _emit_arbiter_audit_event(
                    "arbiter_deny",
                    f"HITL arbiter DENY {tool} (tier={vperm}): {reason}",
                    {"verb": tool, "tier": vperm, "args": args, "reason": reason},
                )
                return reason
            return None  # explicit allow (or no verdict field) -> proceed
        if _HITL_ARBITER_FAIL == "closed":
            reason = (f"'{tool}' blocked: policy arbiter returned HTTP {r.status_code} "
                      f"(fail-closed).")
            _emit_arbiter_audit_event(
                "arbiter_block",
                f"HITL arbiter HTTP {r.status_code} block for {tool}",
                {"verb": tool, "tier": vperm, "status_code": r.status_code, "args": args},
            )
            return reason
        return None
    except Exception as e:  # noqa: BLE001 -- fail-closed
        log.warning("HITL arbiter unreachable for %s: %s", tool, e)
        if _HITL_ARBITER_FAIL == "closed":
            reason = f"'{tool}' blocked: policy arbiter unreachable (fail-closed)."
            _emit_arbiter_audit_event(
                "arbiter_unreachable",
                f"HITL arbiter unreachable block for {tool}: {e}",
                {"verb": tool, "error": str(e), "args": args},
            )
            return reason
        return None

def _agent_rbac_filter(aname: str, tools: list) -> list:
    if not aname or not tools:
        return tools
    cfg = _AGENT_REGISTRY.get(aname) or {}
    denied = {str(v) for v in (cfg.get("denied_verbs") or [])}
    allowed = {str(v) for v in (cfg.get("allowed_verbs") or [])}
    max_perm = str(cfg.get("max_permission") or "").strip().lower()
    ceiling = _pdp.resolve_ceiling(max_perm, _PERMISSION_TIERS)
    if not denied and not allowed and ceiling is None:
        return tools
    out = []
    for t in tools:
        nm = ((t.get("function") or {}).get("name") if isinstance(t, dict) else "") or ""
        vperm = str((_VERB_CATALOG.get(nm) or {}).get("permission", "read")).lower()
        if _pdp.decide(nm, in_catalog=nm in _VERB_CATALOG, verb_perm=vperm,
                       denied=denied, allowed=allowed, ceiling_rank=ceiling,
                       tiers=_PERMISSION_TIERS).allow:
            out.append(t)
    if len(out) != len(tools):
        log.info("agent RBAC: %s surface %d -> %d (denied=%d allowed=%d max_perm=%s)",
                 aname, len(tools), len(out), len(denied), len(allowed),
                 max_perm or "-")
    return out

def _match_user_cfg() -> tuple:
    """WS-6/WS-A9: resolve the [users.<name>] policy for the CURRENT request's
    principal (the surface-claimed user_name/user_email in _client_env_var).
    Returns (label, cfg) or ("", None) when no entry matches. Shared by the
    user-axis surface filter AND the dispatch-time PDP so both enforce ONE policy
    (surface and dispatch can never diverge)."""
    env = _client_env_var.get() if isinstance(_client_env_var.get(), dict) else {}
    uname = str((env or {}).get("user_name") or "").strip().lower()
    uemail = str((env or {}).get("user_email") or "").strip().lower()
    if not uname and not uemail:
        return "", None
    users = _toml_section("users") or {}
    if not isinstance(users, dict) or not users:
        return "", None
    for k, v in users.items():
        if not isinstance(v, dict):
            continue
        kk = str(k).strip().lower()
        _vemail = str(v.get("email", "")).strip().lower()
        if (kk and kk in (uname, uemail)) or (uemail and _vemail == uemail):
            return (uname or uemail), v
    return "", None

def _user_rbac_filter(tools: list) -> list:
    if not tools:
        return tools
    label, cfg = _match_user_cfg()
    if not cfg:
        return tools
    denied = {str(v) for v in (cfg.get("denied_verbs") or [])}
    allowed = {str(v) for v in (cfg.get("allowed_verbs") or [])}
    max_perm = str(cfg.get("max_permission") or "").strip().lower()
    ceiling = _pdp.resolve_ceiling(max_perm, _PERMISSION_TIERS)  # WS-A9: fail-closed
    if not denied and not allowed and ceiling is None:
        return tools
    out = []
    for t in tools:
        nm = ((t.get("function") or {}).get("name") if isinstance(t, dict) else "") or ""
        vperm = str((_VERB_CATALOG.get(nm) or {}).get("permission", "read")).lower()
        if _pdp.decide(nm, in_catalog=nm in _VERB_CATALOG, verb_perm=vperm,
                       denied=denied, allowed=allowed, ceiling_rank=ceiling,
                       tiers=_PERMISSION_TIERS).allow:
            out.append(t)
    if len(out) != len(tools):
        log.info("user RBAC: %s surface %d -> %d (denied=%d allowed=%d max_perm=%s)",
                 label, len(tools), len(out), len(denied), len(allowed), max_perm or "-")
    return out

_PDP_AUDIT_ALLOW = str((_toml_section("ai") or {}).get("pdp_audit_allow")
                       or "").strip().lower() in ("1", "true", "yes", "on")

_QUOTA_TRACKERS: dict = {}
_QUOTA_HYDRATED: set = set()
_QUOTA_LEDGER: dict = {}   # principal -> {"window_start": float, "spent": float}
_QUOTA_PERSIST = False     # set by quota_preload() once the store is reachable

async def quota_preload() -> int:
    """Startup: read every persisted budget window before the first dispatch.

    Degrades open: an unreachable store means every principal starts at zero."""
    global _QUOTA_PERSIST
    try:
        rows = await _mios_pg.execute(
            "SELECT principal, window_start, spent FROM quota_ledger", fetch=True)
    except Exception:  # noqa: BLE001 -- degrade-open
        return 0
    if rows is None:
        return 0
    _QUOTA_PERSIST = True
    for r in rows:
        name = str((r or {}).get("principal") or "")
        if not name:
            continue
        _QUOTA_LEDGER[name] = {
            "window_start": float((r or {}).get("window_start") or 0.0),
            "spent": float((r or {}).get("spent") or 0.0),
        }
    return len(_QUOTA_LEDGER)

def _quota_load(principal: str):
    return _QUOTA_LEDGER.get(str(principal or ""))

def _quota_save(principal: str, window_start: float, spent: float) -> None:
    """Write-through: the in-process map first, then a fire-and-forget upsert."""
    name = str(principal or "")
    _QUOTA_LEDGER[name] = {"window_start": float(window_start), "spent": float(spent)}
    if not _QUOTA_PERSIST:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:      # no loop (a sync test / CLI) -> in-memory only
        return
    loop.create_task(_mios_pg.execute(
        "INSERT INTO quota_ledger (principal, window_start, spent, updated_at) "
        "VALUES (%(p)s, %(w)s, %(s)s, now()) "
        "ON CONFLICT (principal) DO UPDATE SET window_start = EXCLUDED.window_start, "
        "spent = EXCLUDED.spent, updated_at = now()",
        {"p": name, "w": float(window_start), "s": float(spent)}))

def _quota_hydrate(ulabel: str, tracker, now: float) -> None:
    """Seat a persisted budget window on first use. Degrades open."""
    if ulabel in _QUOTA_HYDRATED:
        return
    _QUOTA_HYDRATED.add(ulabel)
    try:
        row = _quota_load(ulabel)
        if row:
            tracker.restore(ulabel, row.get("window_start", 0),
                            row.get("spent", 0), now)
    except Exception:  # noqa: BLE001 -- degrade-open
        pass

def _quota_persist(ulabel: str, tracker) -> None:
    """Write the principal's budget window back. Degrades open."""
    try:
        snap = tracker.snapshot(ulabel)
        _quota_save(ulabel, snap["window_start"], snap["spent"])
    except Exception:  # noqa: BLE001 -- degrade-open
        pass

def _quota_for(ulabel: str, ucfg: dict):
    """Return the QuotaTracker for a user, or None when that user has no limits
    configured (rpm_limit/daily_budget both <= 0 -> unlimited -> skip)."""
    rpm = int((ucfg or {}).get("rpm_limit", 0) or 0)
    budget = float((ucfg or {}).get("daily_budget", 0) or 0.0)
    if rpm <= 0 and budget <= 0:
        return None
    tr = _QUOTA_TRACKERS.get(ulabel)
    if tr is None:
        tr = mios_quota.QuotaTracker(rpm_limit=rpm, daily_budget=budget)
        _QUOTA_TRACKERS[ulabel] = tr
    return tr

def _dispatch_quota_reason(verb: str) -> "Optional[str]":
    """WS-6 per-user rate/budget gate at the dispatch chokepoint. Counts one
    request per verb dispatch for the matched [users.*] principal; DENY when over
    the user's rpm_limit / daily_budget. Returns a refusal reason on deny, else
    None. INERT when the principal has no quota config (the default), and
    degrade-open (a quota bug must never block real work)."""
    try:
        ulabel, ucfg = _match_user_cfg()
        if not ucfg:
            return None
        tr = _quota_for(ulabel, ucfg)
        if tr is None:
            return None
        now = time.time()
        _quota_hydrate(ulabel, tr, now)
        v = tr.check(ulabel, now)
        if not v.allowed:
            return (f"quota exceeded for user '{ulabel}' ({v.reason}); "
                    f"'{verb}' was NOT executed.")
        _quota_persist(ulabel, tr)
        return None
    except Exception:  # noqa: BLE001 -- degrade-open: a quota bug never blocks work
        return None

def _dispatch_pdp_reason(verb: str) -> "Optional[str]":
    try:
        in_cat = verb in _VERB_CATALOG
        vperm = str((_VERB_CATALOG.get(verb) or {}).get("permission", "read")).lower()
        checks = []
        aname = (_dispatch_agent_var.get() or "").strip()
        if aname:
            checks.append((f"agent '{aname}'", _AGENT_REGISTRY.get(aname) or {}))
        ulabel, ucfg = _match_user_cfg()
        if ucfg:
            checks.append((f"user '{ulabel}'", ucfg))
        for who, cfg in checks:
            d = _pdp.decide(
                verb, in_catalog=in_cat, verb_perm=vperm,
                denied={str(v) for v in (cfg.get("denied_verbs") or [])},
                allowed={str(v) for v in (cfg.get("allowed_verbs") or [])},
                ceiling_rank=_pdp.resolve_ceiling(
                    str(cfg.get("max_permission") or ""), _PERMISSION_TIERS),
                tiers=_PERMISSION_TIERS)
            if not d.allow:
                return (f"'{verb}' is not permitted for {who} ({d.rule}): "
                        f"{d.reason}. It was NOT executed.")
            if _PDP_AUDIT_ALLOW:
                _db_fire(_db_post(_db_create("event", {
                    "source": "agent-pipe", "kind": "pdp_allow", "severity": "info",
                    "summary": f"PDP allow {verb} for {who}",
                    "payload": {"verb": verb, "who": who, "rule": d.rule},
                }, now_fields=("ts",))))
        return None
    except Exception:  # noqa: BLE001 -- degrade-open: a PDP bug never blocks work
        return None
