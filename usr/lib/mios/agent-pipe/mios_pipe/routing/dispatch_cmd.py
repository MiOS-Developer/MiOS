# AI-hint: Verb -> bash COMMAND BUILDER, extracted VERBATIM from mios_dispatch.py (task_251).
# AI-doc: usr/share/doc/mios/manual/routing.md
"""Verb -> bash command builder (extracted from the dispatch chokepoint)."""

from __future__ import annotations

import base64
import hashlib
import os
import re
import shlex
import uuid
from typing import Optional

import mios_sandbox
from mios_argval import _arg_with_synonyms
from mios_template import _template_to_cmd

# Injected by configure(); the names match mios_dispatch's exactly so the moved
# bodies stay byte-identical.
_VERB_CATALOG: dict = {}
SANDBOX_ENFORCE = False
_SANDBOX_SELF_CONFINED: tuple = ()

_INJECTED = frozenset(("_VERB_CATALOG", "SANDBOX_ENFORCE", "_SANDBOX_SELF_CONFINED"))

def configure(*, verb_catalog=None, sandbox_enforce=None,
              sandbox_self_confined=None) -> None:
    """Inject the three server-owned values the builder reads, under their exact
    original names. mios_dispatch.configure forwards to this, so a caller that
    configures the dispatcher configures the builder with it."""
    g = globals()
    if verb_catalog is not None:
        g["_VERB_CATALOG"] = verb_catalog
    if sandbox_enforce is not None:
        g["SANDBOX_ENFORCE"] = sandbox_enforce
    if sandbox_self_confined is not None:
        g["_SANDBOX_SELF_CONFINED"] = sandbox_self_confined

def _dispatch_sandbox_profile(tool: str) -> "mios_sandbox.SandboxProfile":
    """Resolve the WS-A13 confinement profile for `tool`: its [verbs.*].permission
    tier, with an optional [verbs.*].sandbox_profile explicit override. Fail-closed
    in mios_sandbox (unknown tier/override -> strict)."""
    vcfg = _VERB_CATALOG.get(tool) or {}
    return mios_sandbox.resolve_profile(
        str(vcfg.get("permission", "read")).lower(),
        explicit=vcfg.get("sandbox_profile"))

def _sandbox_wrap_cmd(tool: str, cmd: str,
                      profile: "mios_sandbox.SandboxProfile",
                      session_id: Optional[str] = None) -> "tuple":
    import subprocess
    cephfs_enable = os.environ.get("MIOS_STORAGE_CEPHFS_ENABLE", "false").lower() in ("true", "1", "yes", "on")
    if cephfs_enable:
        # A digest preserves stable isolation for arbitrary external IDs without
        # separator filtering, truncation aliases or shell-significant Unicode.
        sess_id = hashlib.sha256((session_id or uuid.uuid4().hex).encode("utf-8")).hexdigest()[:32]
        uid = os.getuid() if hasattr(os, "getuid") else 1000
        runtime_dir = f"/run/user/{uid}/session-{sess_id}"
        try:
            subprocess.run(["systemd-run", "--user", "--scope", "-p", f"RuntimeDirectory=session-{sess_id}", "true"], capture_output=True, check=False)
        except Exception:
            try:
                os.makedirs(runtime_dir, exist_ok=True)
                os.chmod(runtime_dir, 0o700)
            except Exception:
                pass
        cmd = f"XDG_RUNTIME_DIR={runtime_dir} " + cmd

    opted_in = bool((_VERB_CATALOG.get(tool) or {}).get("sandbox_profile"))
    if not (SANDBOX_ENFORCE and opted_in and profile.confined):
        return cmd, None
    if any(w in cmd for w in _SANDBOX_SELF_CONFINED):
        return cmd, None
    ws = mios_sandbox.workspace_path(tool, uuid.uuid4().hex)
    prefix = mios_sandbox.sandbox_exec_prefix(profile, workspace=ws)
    if not prefix:
        return cmd, None
    return " ".join(shlex.quote(p) for p in prefix) + " " + cmd, ws

def normalize_container_exec(script: str) -> str:
    script = re.sub(r'\bdocker(\.exe)?\b', 'podman', script, flags=re.IGNORECASE)

    script = re.sub(r'\b(mios-)?code-server\b', 'mios-agents', script, flags=re.IGNORECASE)

    def clean_flags(match):
        flag_str = match.group(2)
        if flag_str.startswith('--'):
            if 'tty' in flag_str.lower():
                return match.group(1) + ' exec'
            return match.group(0)
        cleaned = re.sub(r'[tT]', '', flag_str)
        if cleaned == '-':
            return match.group(1) + ' exec'
        return match.group(1) + ' exec ' + cleaned

    script = re.sub(r'\b(podman)\s+exec\s+(\-[a-zA-Z]+|\-\-tty\b)', clean_flags, script, flags=re.IGNORECASE)

    script = re.sub(
        # A flag's ARGUMENT may not start with '-'; that would be the next flag.
        # With [^\s]+ both parses were legal and the group backtracked
        # exponentially on model-controlled input (~1.64^n). See tasks.jsonl task_280.
        r'\b(podman\s+exec\s+(?:-[a-zA-Z\d\-]+(?:\s+[^-\s]\S*)?\s+)*[\w\-\.]+)\s+(bash|sh|zsh|/bin/bash|/bin/sh|/bin/zsh)(\s+-[a-zA-Z\d\-]+)*\s*$',
        r'\1 true',
        script,
        flags=re.IGNORECASE | re.MULTILINE
    )
    return script

def _build_dispatch_cmd(tool: str, args: dict) -> Optional[str]:
    """Map verb name + args -> the bash command line the launcher
    broker executes. Kept in lockstep with the OWUI pipe's
    _dispatch_mios_verb. Returns None for unknown verbs."""
    if tool in ("powershell_run", "run_code", "code_mode"):
        for key in ("script", "code"):
            if key in args and isinstance(args[key], str):
                args[key] = normalize_container_exec(args[key])
    _GUARDED_VERBS = {"launch_app", "window_op"}
    _tmpl = (_VERB_CATALOG.get(tool) or {}).get("cmd")
    if _tmpl and tool not in _GUARDED_VERBS:
        _rendered = _template_to_cmd(tool, _tmpl, args)
        if _rendered:
            return _rendered
    if tool == "launch_app":
        name = _arg_with_synonyms(tool, "name", args).strip()
        if name and ("/" in name or "\\" in name):
            base = os.path.basename(name.rstrip("/\\")) or name
            for suf in (".exe", ".desktop", ".lnk"):
                if base.lower().endswith(suf):
                    base = base[: -len(suf)]
                    break
            name = base
        if not name:
            return None
        norm = name.lower().replace("-", "_").rstrip("s")
        if norm in _VERB_CATALOG or norm.rstrip("_") in {
            v.replace("-", "_").rstrip("s") for v in _VERB_CATALOG
        }:
            return None
        _clean = dict(args, name=name)
        extra_args = args.get("args") or []
        _cat = _VERB_CATALOG.get("launch_app") or {}
        if extra_args:
            _tmpl = _cat.get("cmd_args") or _cat.get("cmd")
        else:
            _tmpl = _cat.get("cmd")
        if _tmpl:
            return _template_to_cmd("launch_app", _tmpl, _clean)
        return None
    if tool == "window_op":
        op = str(args.get("op", "focus")).lower()
        _cat = _VERB_CATALOG.get("window_op") or {}
        if op == "focus":
            pos = str(args.get("position", "default")).lower()
            if pos in ("as-is", "default", ""):
                _tmpl = _cat.get("cmd")
            else:
                _tmpl = _cat.get("cmd_positioned")
        elif op == "move-pixel":
            _tmpl = _cat.get("cmd_pixel")
        elif op == "resize":
            w = int(args.get("width", 0))
            h = int(args.get("height", 0))
            if w <= 0 or h <= 0:
                return None
            _tmpl = _cat.get("cmd_resize")
        else:
            _tmpl = _cat.get("cmd")
        if _tmpl:
            return _template_to_cmd("window_op", _tmpl, args)
        return None
    if tool == "os_recipe":
        name = _arg_with_synonyms(tool, "name", args).strip()
        if not name:
            return None
        params = args.get("params") or {}
        if not isinstance(params, dict):
            params = {}
        kv_args = " ".join(
            f"{shlex.quote(str(k))}={shlex.quote(str(v))}"
            for k, v in params.items()
        )
        target_os = str(args.get("os") or "").strip().lower()
        if (not target_os
                and name in {"show-network", "disk-usage", "list-drives", "show-process"}
                and os.path.exists("/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")):
            target_os = "windows"
        os_flag = f"--os {shlex.quote(target_os)} " if target_os in ("linux", "windows") else ""
        return f"mios-os-recipe --json {os_flag}{shlex.quote(name)} {kv_args}".strip()
    if tool == "pkg":
        action = str(args.get("action") or "").strip().lower()
        backend = str(args.get("backend") or "auto").strip().lower()
        pid = _arg_with_synonyms(tool, "id", args).strip()
        query = _arg_with_synonyms(tool, "query", args).strip()
        if backend == "auto":
            ref = pid or query
            backend = "flatpak" if ("/" in ref or ref.startswith("org.")) else "winget"
        if backend not in ("winget", "flatpak"):
            return None
        legacy = {
            "search":     f"{backend}_search",
            "list":       f"{backend}_list",
            "show":       f"{backend}_show",
            "install":    f"{backend}_install",
            "upgrade":    f"{backend}_upgrade",
            "uninstall":  f"{backend}_uninstall",
            "preflight":  "flatpak_preflight",  # winget has no analog
        }.get(action)
        if not legacy:
            return None
        forwarded = dict(args)
        if action == "search" and query:
            forwarded["query"] = query
        if pid:
            forwarded["id"] = pid
        return _build_dispatch_cmd(legacy, forwarded)
    if tool == "pc_key":
        key = str(args.get("key", "")).strip()
        if "+" in key:
            return f"mios-pc-control key-combo {shlex.quote(key)}"
        return f"mios-pc-control key {shlex.quote(key)}"
    if tool == "pc_click":
        x = int(args.get("x", 0))
        y = int(args.get("y", 0))
        button = str(args.get("button", "left")).lower()
        if button not in ("left", "right", "middle"):
            button = "left"
        return f"mios-pc-control click {x} {y} {button}"
    if tool == "text_create":
        path = shlex.quote(str(args.get("path", "")))
        body_b64 = base64.b64encode(
            str(args.get("content", "")).encode("utf-8")).decode()
        return (
            f"echo {shlex.quote(body_b64)} | base64 -d "
            f"| mios-text-edit create {path} --content -"
        )
    if tool == "text_str_replace":
        path = shlex.quote(str(args.get("path", "")))
        old_b64 = base64.b64encode(
            str(args.get("old", "")).encode("utf-8")).decode()
        new_b64 = base64.b64encode(
            str(args.get("new", "")).encode("utf-8")).decode()
        return (
            "_old=$(mktemp); _new=$(mktemp); "
            f"echo {shlex.quote(old_b64)} | base64 -d > $_old; "
            f"echo {shlex.quote(new_b64)} | base64 -d > $_new; "
            f"mios-text-edit str_replace {path} --old @$_old --new @$_new; "
            "_rc=$?; rm -f $_old $_new; exit $_rc"
        )
    if tool == "text_insert":
        path = shlex.quote(str(args.get("path", "")))
        line = int(args.get("line", 0))
        body_b64 = base64.b64encode(
            str(args.get("content", "")).encode("utf-8")).decode()
        return (
            f"echo {shlex.quote(body_b64)} | base64 -d "
            f"| mios-text-edit insert {path} --line {line} --content -"
        )
    if tool == "powershell_run":
        script = str(args.get("script", ""))
        if not script.strip():
            return None
        script = normalize_container_exec(script)
        timeout = int(args.get("timeout", 30))
        work_dir = str(args.get("work_dir", "")).strip()
        elevate = bool(args.get("elevate", False))
        script_b64 = base64.b64encode(script.encode("utf-8")).decode()
        cmd = (
            f"echo {shlex.quote(script_b64)} | base64 -d "
            f"| mios-powershell --timeout {timeout} --json"
        )
        if work_dir:
            cmd += f" --work-dir {shlex.quote(work_dir)}"
        if elevate:
            cmd += " --elevate"
        cmd += " -"
        return cmd
    return None
