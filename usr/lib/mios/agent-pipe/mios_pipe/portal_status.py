# AI-hint: Read-only MiOS status & goals view: GET /portal/status (json_schema mios_portal_status) + /portal/status.js from producer snapshots, the SSOT, /.git, the goal record and the task ledger; never runs a goal criterion.
# AI-related: /usr/lib/mios/agent-pipe/mios_pipe/portal_spec.py, /usr/lib/mios/agent-pipe/mios_pipe/routing/portal.py, /usr/lib/mios/schemas/portal_status.schema.json, /usr/lib/mios/agent-pipe/test_mios_portal.py, /usr/share/mios/configurator/mios.html, /usr/libexec/mios/mios-mcp-server, mios.toml [portal.status]
# AI-functions: build_status, status_logic, status_js, status_js_logic

"""Portal status & goals aggregate; each source reads ok, absent, stale, corrupt, refused or unavailable.
Producer snapshots are bound to their owner uid and re-whitelisted field by field.
spec.json is written by mios_pipe.portal_spec (root, an ExecStopPost of mios-podman-ps.service), which also
owns the [portal.status] validation imported here and never imports this module or the web stack.
"""

from __future__ import annotations

import asyncio
import errno
import glob
import json
import logging
import os
import pwd
import re
import shutil
import stat
import subprocess
import threading
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from mios_pipe.portal_spec import SPEC_SCHEMA, _cfg, _file_layers, _mios_toml, _opt, _pin, _safe_rel, _text
from mios_pipe.routing.portal_assets import _PORTAL_STATUS_JS as STATUS_JS

logger = logging.getLogger("mios.portal_status")

AGENTS_SCHEMA = "mios.agents.observation.v1"
SOURCES = ("agents", "spec", "goal", "goals", "tasks", "services")
# The one uid trusted to write spec.json (the mios-podman-ps.service writer runs as root).
_SPEC_UID = 0

# Fixed reason vocabulary: a detail is never an exception string or a path.
D_FRESH = "snapshot fresh"
D_STALE = "snapshot stale"
D_PARTIAL = "some snapshots refused or stale"
D_NO_SNAPSHOT = "no snapshot"
D_UNREADABLE = "snapshot unreadable"
D_OWNER = "snapshot owner mismatch"
D_WORLD = "snapshot world-writable"
D_GROUP = "snapshot group-writable"
D_NOT_FILE = "snapshot not a regular file"
D_OVERSIZE = "snapshot over byte budget"
D_SCHEMA = "snapshot schema mismatch"
D_DIR = "snapshot directory unreadable"
D_CONFIG = "configuration missing or invalid"
D_FAILED = "collector failed"
D_READ = "read"
D_NO_GOAL = "no goal record"
D_GOAL_BAD = "goal record unreadable"
D_NO_DOCS = "no goal documents"
D_NO_LEDGER = "task ledger absent"
D_INVALID = "task ledger has invalid lines"
D_NO_TOOL = "task tool unavailable"
D_NO_SYSTEMCTL = "systemctl unavailable"
DETAILS = frozenset(v for k, v in dict(globals()).items() if k.startswith("D_"))

_AGENTS_FILE = re.compile(r"agents-(\d{1,10})\.json")
_HEX = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_BRANCH = re.compile(r"[A-Za-z0-9._/-]{1,200}")
_IDENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@-]{0,95}")
_PANE = re.compile(r"%\d{1,9}")
_UNIT = re.compile(r"[A-Za-z0-9][A-Za-z0-9:_.@-]{0,200}\.(?:service|socket|target|timer|path|mount)")
_ROW = re.compile(r"^\|\s*([A-Z]{1,4}-\d{1,4})\s*\|")
_ACTIVE = frozenset({"active", "inactive", "failed", "activating", "deactivating"})
_STATUS_KEYS = ("pending", "in_progress", "completed", "incomplete", "cancelled")
_SMALL = 1 << 16
# Fixed safety bounds on record sizes, not display choices (task_rows and agent_rows are SSOT keys).
_MAX_DOCS, _MAX_LIST, _MAX_ROWS = 16, 32, 64

def _ident(value):
    text = _text(value, 96)
    return text if _IDENT.fullmatch(text) else None

_SSOT_MEMO: dict = {}

def _ssot() -> dict:
    """The merged SSOT with portal_spec's pins, re-read when a layer file or mios.d dir changes; load_merged
    alone caches for life."""
    mios_toml = _mios_toml()
    key = tuple((p, _stat_key(p)) for p in [*mios_toml.layer_paths(), *mios_toml._tier_dirs()[1::2]])
    if _SSOT_MEMO.get("key") != key:
        mios_toml.clear_cache()
        _SSOT_MEMO.update(key=key, value=_pin(mios_toml.load_merged(), _file_layers()[1]))
    return _SSOT_MEMO["value"]

def _node_name() -> str:
    try:
        from mios_pipe.scheduler.blades import local_blade_name
        return _text(local_blade_name(), 96) or "unknown"
    except Exception:
        return "unknown"

def _read_bounded(path, budget, expect_uid=None):
    """(bytes, state, detail, mtime). Never follows a final symlink, never raises."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None, "absent", D_NO_SNAPSHOT, None
    except OSError as error:
        if error.errno == errno.ELOOP:
            return None, "refused", D_NOT_FILE, None
        return None, "unavailable", D_UNREADABLE, None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            return None, "refused", D_NOT_FILE, None
        if expect_uid is not None and info.st_uid != expect_uid:
            return None, "refused", D_OWNER, None
        if expect_uid is not None and info.st_mode & stat.S_IWOTH:
            return None, "refused", D_WORLD, None
        if expect_uid is not None and info.st_mode & stat.S_IWGRP:  # the directory's group holds other writers
            return None, "refused", D_GROUP, None
        if info.st_size > budget:
            return None, "refused", D_OVERSIZE, None
        chunks, size = [], 0
        while size <= budget:
            chunk = os.read(fd, min(_SMALL, budget + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        if size > budget:
            return None, "refused", D_OVERSIZE, None
        return b"".join(chunks), "ok", D_READ, int(info.st_mtime)
    except OSError:
        return None, "unavailable", D_UNREADABLE, None
    finally:
        os.close(fd)

def _read_snapshot(path, expect_uid, budget):
    """(dict, state, detail) for a producer snapshot bound to expect_uid."""
    raw, state, detail, _ = _read_bounded(path, budget, expect_uid)
    if raw is None:
        return None, state, detail
    try:
        doc = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None, "corrupt", D_UNREADABLE
    if not isinstance(doc, dict) or type(doc.get("ts")) is not int:
        return None, "corrupt", D_SCHEMA
    return doc, "ok", D_FRESH

def _rank(states):
    for state in ("refused", "corrupt", "unavailable", "absent"):
        if state in states:
            return state
    return "absent"

def _empty_agents(state):
    return {"state": state, "age_s": None, "total": 0, "online": 0, "pending": 0,
            "rows": [], "sources": []}

_RESERVED = frozenset({"local", "unknown"})

def _row_source(raw, member_ids):
    """'local' for the publisher's own relay (a state path, or 'local' once reduced), a member id its
    trusted frame declared, else 'unknown': an undeclared head is never passed through."""
    if not isinstance(raw, str) or not raw or raw == "local" or raw.startswith("/"):
        return "local"
    head = raw.split("/", 1)[0]
    return head if head in member_ids else "unknown"

def _trusted_uids(doc):
    """Publishers whose federation sources are believed: root and the relay owner ([identity].username).
    Any other mios-ai member's frame still shows its own relay's agents, never peers or nodes."""
    uids = {0}
    name = (doc.get("identity") or {}).get("username")
    if isinstance(name, str) and name:
        try:
            uids.add(pwd.getpwnam(name).pw_uid)
        except KeyError:
            pass
    return uids

def _project_agents(frames, cap, stale=(), trusted=frozenset({0})):
    """Re-whitelist registrations, reported subagents and tmux-slot subagents of [(uid, frame)].
    Rows are keyed by (publisher uid, source, agent id). Member ids, presence, node cards and a row's
    online flag and node come only from a trusted publisher; a member is present when ready, partial
    or contributing rows."""
    members, presence = {}, {}
    for uid, frame in frames:
        ids = members.setdefault(uid, set())
        for row in frame.get("sources") or []:
            if not isinstance(row, dict):
                continue
            if row.get("kind") != "mcp":
                presence["local"] = "local"
                continue
            ident, state = _ident(row.get("source")), row.get("state")
            if uid not in trusted or not ident or ident in _RESERVED or state == "unsupported":
                continue  # an untrusted claim, or a plain MCP server that is no relay peer
            ids.add(ident)
            if state == "ready" or presence.get(ident) == "reachable":
                presence[ident] = "reachable"
            elif state == "partial":
                presence[ident] = "unknown"
    rows, seen, total, online, pending = [], set(), 0, 0, 0
    for index, (uid, frame) in enumerate(frames):
        dead = index < len(stale) and stale[index]
        member_ids = members[uid]
        for row in frame.get("agents") or []:
            agent_id = _text(row.get("agent_id"), 96) if isinstance(row, dict) else ""
            if not agent_id:
                continue
            source = _row_source(row.get("source"), member_ids)
            if (uid, source, agent_id) in seen:
                continue
            seen.add((uid, source, agent_id))
            count = row.get("pending")
            count = count if type(count) is int and count >= 0 else 0
            vouched = uid in trusted and source != "unknown"  # else no trusted frame vouches for it
            is_online = None if dead or row.get("stale") or not vouched else (row.get("online") is True)
            total += 1
            online += is_online is True
            pending += count
            parent = _opt(row.get("parent_agent_id"), 96)
            rows.append({"agent_id": agent_id, "kind": _text(row.get("kind"), 64),
                         "label": _text(row.get("label")), "online": is_online, "pending": count,
                         "parent_agent_id": parent, "node": _opt(row.get("node"), 96) if vouched else None,
                         "session_name": _opt(row.get("session_name"), 96), "source": source,
                         "subagent": parent is not None})
        for row in frame.get("subagents") or []:
            if not isinstance(row, dict) or row.get("agent_id"):
                continue  # reported subagents are already registrations above
            pane = _text(row.get("pane"), 12) if _PANE.fullmatch(_text(row.get("pane"), 12)) else None
            source = _row_source(row.get("source"), member_ids)
            if not pane or (uid, source, "pane:" + pane) in seen:
                continue
            seen.add((uid, source, "pane:" + pane))
            parent = _text(row.get("parent_pane"), 12) if _PANE.fullmatch(_text(row.get("parent_pane"), 12)) else None
            rows.append({"agent_id": "pane:" + pane, "kind": _text(row.get("agent_kind"), 64) or "tmux",
                         "label": _text(row.get("role"), 32) or "slot", "online": None, "pending": 0,
                         "parent_agent_id": "pane:" + parent if parent else None, "node": None,
                         "session_name": _opt(row.get("session_name"), 96), "source": source,
                         "subagent": True})
    rows.sort(key=lambda r: (r["online"] is not True, r["subagent"], r["agent_id"]))
    contributors = sorted({r["source"] for r in rows} - _RESERVED)
    for name in contributors:  # a member with rows (an unavailable peer's cached rows too) is present
        presence.setdefault(name, "unknown")
    sources = [{"source": name, "presence": presence[name]} for name in sorted(presence)]
    return rows[:cap], total, online, pending, sources, contributors

def _agents(doc, cfg, now):
    directory = cfg["snapshot_dir"]
    try:
        names = sorted(os.listdir(directory))
    except FileNotFoundError:
        return _empty_agents("absent"), "absent", D_NO_SNAPSHOT, None
    except OSError:
        return _empty_agents("unavailable"), "unavailable", D_DIR, None
    frames, rejected = [], []
    for name in names:
        match = _AGENTS_FILE.fullmatch(name)
        if not match:
            continue
        uid = int(match.group(1))
        frame, state, detail = _read_snapshot(os.path.join(directory, name), uid, cfg["max_state_bytes"])
        if frame is not None and (frame.get("schema") != AGENTS_SCHEMA or not isinstance(frame.get("agents"), list)):
            frame, state, detail = None, "corrupt", D_SCHEMA
        if frame is None:
            rejected.append((state, detail))
        else:
            frames.append((uid, frame))
    if not frames:
        state = _rank({s for s, _ in rejected})
        detail = next((d for s, d in rejected if s == state), D_NO_SNAPSHOT)
        return _empty_agents(state), state, detail, None
    # Per-frame staleness: a dead publisher's rows lose their online flag and the oldest frame sets the age.
    ages = [max(0, now - frame["ts"]) for _, frame in frames]
    stale = [age > cfg["stale_after_s"] for age in ages]
    rows, total, online, pending, sources, contributors = _project_agents(
        frames, min(cfg["agent_rows"], cfg["max_agents"]), stale, _trusted_uids(doc))
    age, state = max(ages), "stale" if any(stale) else "ok"
    detail = D_PARTIAL if rejected or (any(stale) and not all(stale)) else (D_FRESH if state == "ok" else D_STALE)
    value = {"state": state, "age_s": age, "total": total, "online": online, "pending": pending,
             "rows": rows, "sources": sources, "_from": contributors}
    return value, state, detail, age

def _spec_snapshot(doc, cfg, now):
    snap, state, detail = _read_snapshot(os.path.join(cfg["snapshot_dir"], "spec.json"),
                                         _SPEC_UID, cfg["max_state_bytes"])
    if snap is None:
        return None, state, detail, None
    bootc, dirty = snap.get("bootc"), snap.get("git_dirty")
    if (snap.get("schema") != SPEC_SCHEMA or not isinstance(bootc, dict)
            or bootc.get("state") not in ("booted", "not-bootc", "unavailable")
            or not (dirty is None or type(dirty) is int)):
        return None, "corrupt", D_SCHEMA, None
    age = max(0, now - snap["ts"])
    fresh = age <= cfg["stale_after_s"]
    return {**snap, "age_s": age}, "ok" if fresh else "stale", D_FRESH if fresh else D_STALE, age

def _read_text(path, limit=_SMALL):
    with open(path, "rb") as fh:
        return fh.read(limit).decode("utf-8", "replace")

def _git_head(root):
    """(commit, branch) of a work tree from its .git files; no git process."""
    gitdir = os.path.join(root, ".git")
    try:
        if os.path.isfile(gitdir):
            line = _read_text(gitdir).strip()
            if not line.startswith("gitdir:"):
                return None, None
            gitdir = os.path.normpath(os.path.join(root, line[len("gitdir:"):].strip()))
        common = gitdir
        if os.path.isfile(os.path.join(gitdir, "commondir")):
            common = os.path.normpath(os.path.join(gitdir, _read_text(os.path.join(gitdir, "commondir")).strip()))
        head = _read_text(os.path.join(gitdir, "HEAD")).strip()
    except OSError:
        return None, None
    if _HEX.fullmatch(head):
        return head, None
    if not head.startswith("ref: refs/heads/"):
        return None, None
    ref = head[len("ref: "):]
    branch = ref[len("refs/heads/"):]
    if not _BRANCH.fullmatch(branch) or ".." in branch:
        return None, None
    for base in (gitdir, common):
        try:
            sha = _read_text(os.path.join(base, ref)).strip()
        except OSError:
            continue
        if _HEX.fullmatch(sha):
            return sha, branch
    try:
        for line in _read_text(os.path.join(common, "packed-refs"), 1 << 24).splitlines():
            sha, _, name = line.partition(" ")
            if name.strip() == ref and _HEX.fullmatch(sha):
                return sha, branch
    except OSError:
        pass
    return None, branch

def _os_version():
    for path in ("/etc/os-release", "/usr/lib/os-release"):
        try:
            rows = dict(line.split("=", 1) for line in _read_text(path).splitlines() if "=" in line)
        except OSError:
            continue
        value = rows.get("PRETTY_NAME") or rows.get("VERSION_ID") or ""
        return _opt(value.strip().strip("\"'"), 120)
    return None

def _remote_names(doc, contributors, local):
    """[blades] members plus the federation sources that contributed agent rows."""
    names = [_ident(name) for name, table in sorted((doc.get("blades") or {}).items())
             if isinstance(table, dict) and {"vram_budget_mb", "load_ceil"} & set(table)]
    names += list(contributors)
    out = []
    for name in names:
        if name and name != local and name not in out:
            out.append(name)
    return out

_UNKNOWN_NODE = {"state": "unknown", "mios_version": None, "image_ref": None, "image_branch": None,
                 "profile": None, "os_version": None, "git_head": None, "git_branch": None,
                 "git_dirty": None, "bootc_state": "unknown", "booted_image": None, "booted_digest": None,
                 "staged_image": None, "rollback_image": None, "spec_age_s": None}

def _nodes(doc, cfg, local, spec, spec_state, contributors):
    """This node from the SSOT, /.git and spec.json; peers stay 'unknown' (no authenticated spec feed)."""
    meta, image = doc.get("meta") or {}, doc.get("image") or {}
    head, branch = _git_head(cfg["repo_root"])
    node = {"name": local, "local": True, **_UNKNOWN_NODE,
            "state": spec_state if spec_state in ("ok", "stale") else "unknown",
            "mios_version": _opt(meta.get("mios_version"), 32), "image_ref": _opt(image.get("ref"), 200),
            "image_branch": _opt(image.get("branch"), 64),
            "profile": _opt((doc.get("profiles") or {}).get("default"), 64), "os_version": _os_version(),
            "git_head": head, "git_branch": branch}
    if spec:
        bootc = spec["bootc"]
        node.update(git_dirty=spec["git_dirty"], bootc_state=bootc["state"], spec_age_s=spec["age_s"],
                    booted_image=_opt(bootc.get("booted_image"), 300),
                    booted_digest=_opt(bootc.get("booted_digest"), 100),
                    staged_image=_opt(bootc.get("staged_image"), 300),
                    rollback_image=_opt(bootc.get("rollback_image"), 300))
    return [node, *({"name": name, "local": False, **_UNKNOWN_NODE}
                    for name in _remote_names(doc, contributors, local))]

def _goal(doc, cfg, now):
    raw, state, _, mtime = _read_bounded(os.path.join(cfg["repo_root"], cfg["goal_state"]),
                                         cfg["max_state_bytes"])
    if raw is None:
        return None, state, D_NO_GOAL if state == "absent" else D_GOAL_BAD, None
    try:
        record = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None, "corrupt", D_GOAL_BAD, None
    if not isinstance(record, dict) or not isinstance(record.get("objective"), str):
        return None, "corrupt", D_GOAL_BAD, None
    criteria = []
    for row in (record.get("criteria") or [])[:_MAX_ROWS]:
        if isinstance(row, dict):
            passed = row.get("passed")  # goal.py records passed=False, details=None until evaluated
            criteria.append({"id": _text(row.get("id"), 32), "criterion_type": _text(row.get("criterion_type"), 32),
                             "description": _text(row.get("description"), 400),
                             "passed": passed if type(passed) is bool and row.get("details") is not None else None,
                             "details": _opt(row.get("details"), 300)})
    value = {"path": cfg["goal_state"], "objective": _text(record["objective"], 1200),
             "stopping_condition": _text(record.get("stopping_condition"), 600),
             "status": _text(record.get("status"), 32) or "UNKNOWN",
             "created_at": _opt(record.get("created_at"), 40), "completed_at": _opt(record.get("completed_at"), 40),
             "evaluated": "recorded", "criteria": criteria,
             "non_goals": [_text(v, 300) for v in (record.get("non_goals") or [])[:_MAX_LIST] if isinstance(v, str)]}
    return value, "ok", D_READ, max(0, now - mtime)

def _goal_doc(path, rel, budget):
    raw, _, _, mtime = _read_bounded(path, budget)
    if raw is None:
        return None, None
    title, conditions = None, []
    for line in raw.decode("utf-8", "replace").splitlines():
        if title is None and line.startswith("# "):
            title = _text(line[2:], 160)
        if _ROW.match(line) and len(conditions) < _MAX_ROWS:
            cells = [cell.strip() for cell in re.split(r"(?<!\\)\|", line.strip().strip("|"))]
            if len(cells) >= 2 and cells[1]:
                condition = cells[1].replace("\\|", "|").replace("**", "")
                conditions.append({"id": cells[0], "condition": _text(condition, 600)})
    return {"path": rel, "title": title or rel, "conditions": conditions}, mtime

def _goals(doc, cfg, now):
    root = cfg["repo_root"]
    docs, newest = [], None
    for path in sorted(glob.glob(os.path.join(root, cfg["goal_docs"])))[:_MAX_DOCS]:
        rel = os.path.relpath(path, root)
        if not _safe_rel(rel):
            continue
        row, mtime = _goal_doc(path, rel, cfg["max_state_bytes"])
        if row is not None:
            docs.append(row)
            newest = mtime if newest is None else max(newest, mtime)
    if not docs:
        return [], "absent", D_NO_DOCS, None
    return docs, "ok", D_READ, max(0, now - newest)

def _empty_tasks(state):
    return {"state": state, "total": 0, "by_status": {**{k: 0 for k in _STATUS_KEYS}, "other": 0},
            "overrides": None, "invalid_lines": 0, "in_progress": [], "ready": []}

def _task_row(row):
    number = row.get("number")
    return {"id": _text(row.get("id"), 64), "number": number if type(number) is int else None,
            "title": _text(row.get("title"), 200), "priority": _opt(row.get("priority"), 8),
            "owner": _opt(row.get("owner"), 64)}

def _priority_key(row):
    priority = row.get("priority") or ""
    rank = int(priority[1:]) if re.fullmatch(r"P\d", priority) else 9
    return rank, row["number"] if row["number"] is not None else 1 << 30

_LEDGER_MEMO: dict = {}

def _ledger(path, info):
    """Histogram + in-progress rows of tasks.jsonl, memoized on (mtime, size)."""
    key = (path, info.st_mtime_ns, info.st_size)
    if _LEDGER_MEMO.get("key") == key:
        return _LEDGER_MEMO["value"]
    counts, invalid, active = {**{k: 0 for k in _STATUS_KEYS}, "other": 0}, 0, []
    with open(path, "rb") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                row = None
            if not isinstance(row, dict):
                invalid += 1
                continue
            status = row.get("status")
            counts[status if status in _STATUS_KEYS else "other"] += 1
            if status == "in_progress":
                active.append(_task_row(row))
    active.sort(key=_priority_key)
    _LEDGER_MEMO.update(key=key, value=(counts, invalid, active))
    return counts, invalid, active

_TOOL_MEMO: dict = {}

def _task_tool(root, limit, key, timeout):
    """(ready rows, override count) from mios-task, or ([], None) when it is unavailable.
    Memoized on the ledger and TASKS.md stat, so a poll re-runs it only after an edit."""
    exe = shutil.which("mios-task")
    if not exe:
        return [], None
    key = (exe, root, limit, key)
    if _TOOL_MEMO.get("key") == key:
        return _TOOL_MEMO["value"]
    try:
        out = subprocess.run([exe, "next", "--json", "--limit", str(limit), "--root", root],
                             capture_output=True, text=True, timeout=timeout).stdout
        ready = [_task_row(row) for row in json.loads(out) if isinstance(row, dict)][:limit]
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        ready = []
    try:
        out = subprocess.run([exe, "check", "--json", "--root", root],
                             capture_output=True, text=True, timeout=timeout).stdout
        value = json.loads(out).get("overrides")
        overrides = value if type(value) is int else None
    except (OSError, subprocess.SubprocessError, ValueError, AttributeError):
        overrides = None
    _TOOL_MEMO.update(key=key, value=(ready, overrides))
    return ready, overrides

def _stat_key(path):
    try:
        info = os.stat(path)
    except OSError:
        return None
    return info.st_mtime_ns, info.st_size

def _tasks(doc, cfg, now):
    store = (doc.get("tasks") or {}).get("store") or {}
    rel = store.get("path")
    if not _safe_rel(rel):
        return _empty_tasks("unavailable"), "unavailable", D_CONFIG, None
    path = os.path.join(cfg["repo_root"], rel)
    try:
        info = os.stat(path)
    except FileNotFoundError:
        return _empty_tasks("absent"), "absent", D_NO_LEDGER, None
    counts, invalid, active = _ledger(path, info)
    total = sum(counts.values())
    state = "ok" if total or not invalid else "corrupt"
    rendered = os.path.join(cfg["repo_root"], store["doc"]) if _safe_rel(store.get("doc")) else None
    stamp = (info.st_mtime_ns, info.st_size, rendered and _stat_key(rendered))
    ready, overrides = _task_tool(cfg["repo_root"], cfg["task_rows"], stamp, cfg["request_timeout_s"])
    detail = D_INVALID if invalid else (D_NO_TOOL if overrides is None else D_READ)
    value = {"state": state, "total": total, "by_status": counts, "overrides": overrides,
             "invalid_lines": invalid, "in_progress": active[:cfg["task_rows"]], "ready": ready}
    return value, state, detail, max(0, now - int(info.st_mtime))

def _services(doc, cfg, now):
    greenboot = doc.get("greenboot") or {}
    probes = greenboot.get("probe") or {}
    rows = []
    for name in (greenboot.get("critical_services") or [])[:_MAX_LIST]:
        if not isinstance(name, str) or not _IDENT.fullmatch(name):
            continue
        probe = probes.get(name.replace("-", "_")) or probes.get(name) or {}
        unit = probe.get("unit") if isinstance(probe, dict) and isinstance(probe.get("unit"), str) else f"mios-{name}.service"
        if _UNIT.fullmatch(unit):
            rows.append({"name": name, "unit": unit, "active": "unknown"})
    if not rows:
        return rows, "ok", D_READ, 0
    exe = shutil.which("systemctl")
    if not exe:
        return rows, "unavailable", D_NO_SYSTEMCTL, None
    try:
        out = subprocess.run([exe, "is-active", *[row["unit"] for row in rows]],
                             capture_output=True, text=True, timeout=cfg["request_timeout_s"]).stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        return rows, "unavailable", D_NO_SYSTEMCTL, None
    if len(out) != len(rows):  # no bus (a container): one answer per unit, or nothing at all
        return rows, "unavailable", D_NO_SYSTEMCTL, None
    for row, value in zip(rows, out):
        row["active"] = value.strip() if value.strip() in _ACTIVE else "unknown"
    return rows, "ok", D_READ, 0

def _payload(now, node, refresh_s, stale_after_s, nodes, agents, goal, goals, tasks, services, sources):
    return {"object": "mios.portal.status", "schema_version": "1", "ts": now, "refresh_s": refresh_s,
            "stale_after_s": stale_after_s, "node": node, "nodes": nodes, "agents": agents, "goal": goal,
            "goals": goals, "tasks": tasks, "services": services,
            "sources": [{"name": name, "state": sources[name][0], "detail": sources[name][1],
                         "age_s": sources[name][2]} for name in SOURCES]}

def _degraded(now, node, detail, refresh_s=0):
    """Fail-closed body: every source unavailable. refresh_s stays 0 (clients stop polling) only for a
    configuration error; a failed aggregate keeps the configured cadence so a client recovers."""
    return _payload(now, node, refresh_s, 0, [], _empty_agents("unavailable"), None, [],
                    _empty_tasks("unavailable"), [], {name: ("unavailable", detail, None) for name in SOURCES})

def _poll_s():
    """[portal.status].refresh_s when the configuration is valid, else 0; never raises."""
    try:
        cfg = _cfg(_ssot())
        return cfg["refresh_s"] if cfg else 0
    except Exception:
        return 0

def _build(now=None):
    """(payload, cache_s). Each collector failure becomes that source's 'unavailable'."""
    now = int(time.time()) if now is None else now
    node = _node_name()
    try:
        doc = _ssot()
        cfg = _cfg(doc)
    except Exception:
        doc, cfg = None, None
    if cfg is None:
        return _degraded(now, node, D_CONFIG), 0
    sources = {}

    def run(name, collector, fallback):
        try:
            value, state, detail, age = collector(doc, cfg, now)
        except Exception:
            logger.warning("portal status: %s collector failed", name)
            value, state, detail, age = fallback, "unavailable", D_FAILED, None
        sources[name] = (state, detail, age)
        return value

    agents = run("agents", _agents, _empty_agents("unavailable"))
    contributors = agents.pop("_from", [])
    spec = run("spec", _spec_snapshot, None)
    goal = run("goal", _goal, None)
    goals = run("goals", _goals, [])
    tasks = run("tasks", _tasks, _empty_tasks("unavailable"))
    services = run("services", _services, [])
    agents["state"] = sources["agents"][0]
    tasks["state"] = sources["tasks"][0] if sources["tasks"][0] in ("ok", "absent", "corrupt") else "unavailable"
    try:
        nodes = _nodes(doc, cfg, node, spec, sources["spec"][0], contributors)
    except Exception:
        logger.warning("portal status: node collector failed")
        nodes, sources["spec"] = [], ("unavailable", D_FAILED, None)
    return _payload(now, node, cfg["refresh_s"], cfg["stale_after_s"], nodes, agents, goal, goals,
                    tasks, services, sources), cfg["cache_s"]

def build_status(now=None) -> dict:
    return _build(now)[0]

_CACHE: dict = {}
_CACHE_LOCK = threading.Lock()

def _cached_status() -> dict:
    with _CACHE_LOCK:
        hit = _CACHE.get("body")
        if hit is not None and time.monotonic() - _CACHE["at"] < _CACHE["ttl"]:
            return hit
        body, ttl = _build()
        _CACHE.update(body=body, at=time.monotonic(), ttl=ttl)
        return body

def _authed(request) -> bool:
    import mios_portal
    return mios_portal._portal_authed(request)

async def status_logic(request) -> JSONResponse:
    """Authenticated, cached, never-500 status aggregate."""
    if not _authed(request):
        return JSONResponse({"error": "auth required"}, status_code=401)
    try:
        body = await asyncio.to_thread(_cached_status)
    except Exception:
        logger.warning("portal status: aggregate failed")
        body = _degraded(int(time.time()), _node_name(), D_FAILED, await asyncio.to_thread(_poll_s))
    return JSONResponse(body, headers={"Cache-Control": "no-store"})

_RETRY = "var RETRY_MAX_S=0;"

def status_js(refresh_s):
    """The served renderer: a first poll that fails is retried with a doubling backoff capped at
    [portal.status].refresh_s (0, an invalid configuration, retries nothing)."""
    return STATUS_JS.replace(_RETRY, f"var RETRY_MAX_S={int(refresh_s)};", 1)

async def status_js_logic(request) -> Response:
    if not _authed(request):
        return JSONResponse({"error": "auth required"}, status_code=401)
    return Response(status_js(await asyncio.to_thread(_poll_s)), media_type="application/javascript",
                    headers={"Cache-Control": "no-cache"})

status_router = APIRouter()

@status_router.get("/portal/status")
async def portal_status(request: Request) -> JSONResponse:
    """The MiOS status & goals aggregate (json_schema mios_portal_status)."""
    return await status_logic(request)

@status_router.get("/portal/status.js")
async def portal_status_js(request: Request) -> Response:
    """The one status & goals renderer: fills #statussec; mios.html mounts it via MiosStatus.mount."""
    return await status_js_logic(request)
