# AI-hint: Provides typed Python tools for the LLM to directly invoke MiOS shell verbs (launch_app, everything_search, mios_apps, mios_find) via a local Unix sock...
# AI-doc: usr/share/doc/mios/manual/tools.md
"""
title: MiOS Verbs
author: MiOS
version: 1.1.0
description: |
  Native typed tools exposing the MiOS shell verbs (launch_app,
  everything_search, mios_apps, mios_find) to the chat model. The
  model sees them in its tool_calls schema and invokes them
  directly -- no more `terminal: mios-launch beamng` indirection
  through the generic shell tool.

  Dispatch goes through the OPERATOR-side launcher broker (unix
  socket at /run/mios-launcher/launcher.sock, mode 0666, mounted
  into the OWUI container by the Quadlet). The broker runs in
  user@<uid>.service so it inherits full WSLg env (WAYLAND_DISPLAY,
  WSL2_GUI_APPS_ENABLED=1, WSL_INTEROP=/run/WSL/<pid>_interop, the
  whole DBUS session). Without that, in-container subprocess of
  /mnt/m/Programs/Everything/es.exe just returns "no match" -- WSL
  interop only works in the operator's exec context, not inside
  podman's isolated namespace.

  Broker protocol (per mios-launcher-daemon docstring):
    "<one line of shell>\\n"           -> fire-and-forget, "OK\\n" reply
    "CAPTURE: <one line of shell>\\n"  -> sync run, raw stdout+stderr reply

  Operator directive "AI-stack native (Hermes
  tools???!!)". This is the OWUI-native answer; a parallel follow-up
  could expose the same verbs as a stdio MCP server.

  Each tool returns structured JSON the model can reason over
  ({success, target, paths, output, stderr}) so a failure is
  unambiguous -- prior chats had the model claiming "I tried to
  launch X" while the shell call had silently failed.

requirements:
  pydantic
"""

from __future__ import annotations

import json
import os
import shlex
import socket
from typing import Literal, Optional

from pydantic import BaseModel, Field

PositionLiteral = Literal[
    "default",
    "as-is",
    "center", "left", "right", "top", "bottom",
    "top-left", "top-right", "bottom-left", "bottom-right",
    "maximize",
]
CloseModeLiteral = Literal["graceful", "force"]
ScreenshotTargetLiteral = Literal["primary", "active-window", "all-monitors"]
ScreenshotActionLiteral = Literal["save", "clipboard", "open"]

LAUNCHER_SOCKET = os.environ.get(
    "MIOS_LAUNCHER_SOCK", "/run/mios-launcher/launcher.sock"
)

def _broker_send(line: str, timeout: float, capture: bool) -> dict:
    """Talk to the operator-side launcher broker. Returns a dict the
    model can reason over deterministically. Never raises."""
    if not line.strip():
        return {"success": False, "exit_code": -1,
                "stdout": "", "stderr": "empty command"}
    if not os.path.exists(LAUNCHER_SOCKET):
        return {"success": False, "exit_code": -1, "stdout": "",
                "stderr": f"launcher broker socket not present at {LAUNCHER_SOCKET} "
                          "(mios-launcher.service down? container missing the mount?)"}
    if capture:
        payload = (f"CAPTURE_JSON: {line}").encode("utf-8") + b"\n"
    else:
        payload = line.encode("utf-8") + b"\n"
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(LAUNCHER_SOCKET)
        s.sendall(payload)
        chunks: list[bytes] = []
        try:
            while True:
                buf = s.recv(65536)
                if not buf:
                    break
                chunks.append(buf)
        except socket.timeout:
            pass
        finally:
            s.close()
    except OSError as e:
        return {"success": False, "exit_code": -1,
                "stdout": "", "stderr": f"broker connect: {e}"}
    raw = b"".join(chunks).decode("utf-8", errors="replace").strip()
    if capture:
        try:
            j = json.loads(raw) if raw else {}
        except (json.JSONDecodeError, ValueError):
            j = {}
        if not j:
            if raw.startswith("ERROR:"):
                return {"success": False, "exit_code": -1,
                        "stdout": "", "stderr": raw}
            return {"success": True, "exit_code": 0,
                    "stdout": raw[:12000], "stderr": ""}
        exit_code = int(j.get("exit_code", -1))
        return {
            "success": exit_code == 0,
            "exit_code": exit_code,
            "stdout": (j.get("stdout") or "")[:12000],
            "stderr": (j.get("stderr") or "")[:4000],
        }
    if raw.startswith("OK"):
        return {"success": True, "exit_code": 0,
                "stdout": "", "stderr": ""}
    return {"success": False, "exit_code": -1,
            "stdout": "", "stderr": raw or "broker returned no reply"}

class Tools:
    class Valves(BaseModel):
        ENABLED: bool = Field(
            default=True,
            description="Master on/off for all MiOS verbs.",
        )
        LAUNCH_TIMEOUT_S: float = Field(
            default=15.0,
            description="Timeout for launch_app dispatch (Steam/Epic protocol handler returns near-instantly; long timeout only matters if the broker stalls).",
        )
        SEARCH_TIMEOUT_S: float = Field(
            default=20.0,
            description="Timeout for everything_search (Voidtools es.exe; sub-100ms typical).",
        )
        INVENTORY_TIMEOUT_S: float = Field(
            default=60.0,
            description="Timeout for mios_apps inventory (rebuilds Windows app cache on first call after wipe; can take ~30s).",
        )

    def __init__(self) -> None:
        self.valves = self.Valves()

    async def launch_app(self, name: str, __user__: Optional[dict] = None) -> str:
        """Launch a MiOS-registered app by short name. Resolves through
        the canonical mios-launch chain (internal-service alias ->
        URL/URI literal -> Windows GUI shortname -> Windows games
        inventory -> MiOS shim -> Linux GUI -> plain CLI). Steam, Epic,
        GOG, Xbox, Battle.net games all dispatch via the Windows
        protocol handler.

        Uses the launcher broker's CAPTURE mode so the resolved target
        line ("mios-launch: <DisplayName> -> <cmd>") comes back and the
        tool can surface it. Launch is still detached on the broker
        side -- mios-launch exec's into mios-windows which Start-Process
        the URI; the protocol handler takes over and returns instantly.

        :param name: App short name. Case-insensitive substring matches
            against the Windows games inventory; exact for shortnames.
            Examples: "beamng", "notepad", "chromedev", "marvel rivals".
        :return: JSON string with {success, target, stderr}.
            success=true means the launch was DISPATCHED (the protocol
            handler took over); the app may take seconds to render its
            window.
        """
        if not self.valves.ENABLED:
            return json.dumps({"success": False, "stderr": "MiOS verbs disabled by valve"})
        cmd = f"mios-launch {shlex.quote(name)}"
        result = _broker_send(cmd, timeout=self.valves.LAUNCH_TIMEOUT_S, capture=True)
        target = ""
        for line in (result.get("stdout") or "").splitlines():
            if line.startswith("mios-launch:") and " -> " in line:
                target = line.split(" -> ", 1)[1].strip()
                break
        return json.dumps({
            "success": result["success"],
            "target": target,
            "broker_output": result.get("stdout", "")[:600],
            "stderr": result.get("stderr", ""),
        })

    async def everything_search(
        self,
        query: str,
        limit: int = 10,
        ext: str = "",
        __user__: Optional[dict] = None,
    ) -> str:
        """Search the Windows NTFS index via Voidtools Everything CLI.
        Returns one Windows path per stdout line. Sub-100ms typical;
        covers EVERY mounted NTFS drive. ALWAYS prefer this over
        recursive `find` / `dir /s` / `Get-ChildItem -Recurse` when
        looking for any file or installation on the host.

        :param query: Everything query string. Supports substring,
            wildcards ("BeamNG*.exe"), path filters ("path:steamapps"),
            size ("size:>1gb"), modify-date ("dm:today"), etc.
        :param limit: Max results to return (default 10).
        :param ext: Optional comma-separated extension filter
            (e.g. "exe,lnk"). Empty = no filter.
        :return: JSON string with {success, paths[], count, stderr}.
        """
        if not self.valves.ENABLED:
            return json.dumps({"success": False, "stderr": "MiOS verbs disabled by valve"})
        cmd = f"mios-everything {shlex.quote(query)} -n {int(limit)}"
        if ext.strip():
            cmd += f" -ext {shlex.quote(ext.strip())}"
        result = _broker_send(cmd, timeout=self.valves.SEARCH_TIMEOUT_S, capture=True)
        paths = [p for p in (result.get("stdout") or "").splitlines() if p.strip()]
        return json.dumps({
            "success": result["success"] and bool(paths),
            "paths": paths,
            "count": len(paths),
            "stderr": result.get("stderr", ""),
        })

    async def fs_search(
        self,
        query: str,
        limit: int = 20,
        ext: str = "",
        path: str = "",
        type: str = "",
        __user__: Optional[dict] = None,
    ) -> str:
        """Search the Linux filesystem inside the MiOS-DEV / host environment.
        Returns one absolute Linux path per stdout line. Backend prefers
        plocate (sub-100ms) -> locate -> find (POSIX fallback). Default
        scope: /usr /etc /var/lib /opt /home /root /var/log /usr/share/mios
        /var/lib/mios. Skips /proc /sys /run /dev /tmp /mnt /var/cache.

        :param query: substring (case-insensitive) on basename or full path.
        :param limit: max results (default 20).
        :param ext: comma-separated extension filter (e.g. "py,toml").
        :param path: optional subtree to restrict the search to.
        :param type: "f" files only, "d" directories only; empty = both.
        :return: JSON string with {success, paths[], count, stderr}.
        """
        if not self.valves.ENABLED:
            return json.dumps({"success": False, "stderr": "MiOS verbs disabled by valve"})
        cmd = f"mios-locate {shlex.quote(query)} -n {int(limit)}"
        if ext.strip():
            cmd += f" -ext {shlex.quote(ext.strip())}"
        if path.strip():
            cmd += f" -path {shlex.quote(path.strip())}"
        if type.strip() in ("f", "d"):
            cmd += f" -type {type.strip()}"
        result = _broker_send(cmd, timeout=self.valves.SEARCH_TIMEOUT_S, capture=True)
        paths = [p for p in (result.get("stdout") or "").splitlines() if p.strip()]
        return json.dumps({
            "success": result["success"] and bool(paths),
            "paths": paths,
            "count": len(paths),
            "stderr": result.get("stderr", ""),
        })

    async def web_search(
        self,
        query: str,
        limit: int = 5,
        __user__: Optional[dict] = None,
    ) -> str:
        """Search the WEB via the local, self-hosted SearXNG metasearch.

        USE THIS for ANYTHING from the internet or the current world:
        weather, news, current events, event dates/times/locations,
        prices, products, people, places, scores, facts, general
        knowledge -- any question whose answer is not a file on this
        machine. Returns real fetched results so the model can cite the
        source. NEVER invent a live fact (weather, an event schedule, a
        price) when this tool can fetch it; a current/world question
        answered from memory is the stale-data / fabrication defect.

        This is NOT file search -- everything_search / fs_search /
        directory_lookup find FILES on disk and return nothing useful
        for a knowledge or current-events query.

        :param query: the natural-language search query.
        :param limit: max results to return (default 5).
        :return: JSON {success, query, count, answers[], infoboxes[],
            results:[{title,url,content}]}. success=false with an
            `error` field when the web / SearXNG is unreachable -- in
            that case say you could not reach the web; do NOT fabricate.
        """
        if not self.valves.ENABLED:
            return json.dumps({"success": False, "stderr": "MiOS verbs disabled by valve"})
        cmd = f"mios-web-search -n {int(limit)} {shlex.quote(query)}"
        result = _broker_send(cmd, timeout=max(self.valves.SEARCH_TIMEOUT_S, 20), capture=True)
        out = (result.get("stdout") or "").strip()
        try:
            return json.dumps(json.loads(out))
        except Exception:
            return json.dumps({
                "success": False,
                "raw": out[:2000],
                "stderr": result.get("stderr", ""),
            })

    async def mios_apps(
        self,
        filter: str = "",
        __user__: Optional[dict] = None,
    ) -> str:
        """Inventory every installed app across all sources (Steam,
        Epic, Xbox/Microsoft Store, flatpak, RPM with .desktop,
        Windows shortcuts, MiOS shims, CLIs). Cached 5 minutes.

        :param filter: Optional case-insensitive substring filter on
            display name. Empty = full inventory.
        :return: JSON string with {success, output, stderr} where
            output is the formatted inventory block (one section per
            source).
        """
        if not self.valves.ENABLED:
            return json.dumps({"success": False, "stderr": "MiOS verbs disabled by valve"})
        cmd = "mios-apps"
        if filter.strip():
            cmd += f" --filter {shlex.quote(filter.strip())}"
        result = _broker_send(cmd, timeout=self.valves.INVENTORY_TIMEOUT_S, capture=True)
        return json.dumps({
            "success": result["success"],
            "output": (result.get("stdout") or "")[:10000],
            "stderr": result.get("stderr", ""),
        })

    async def mios_find(self, name: str, __user__: Optional[dict] = None) -> str:
        """Look up an app's launch info across all MiOS-known sources.
        Faster than mios_apps when you just need ONE app's metadata
        (~60ms vs ~2s). Returns the launch command + source category
        if found.

        :param name: App short name or display-name substring.
        :return: JSON string with {success, output, stderr}.
            If found, output is the launch hint; if not, suggested
            next steps (try everything_search with broader query, etc.).
        """
        if not self.valves.ENABLED:
            return json.dumps({"success": False, "stderr": "MiOS verbs disabled by valve"})
        cmd = f"mios-find {shlex.quote(name)}"
        result = _broker_send(cmd, timeout=self.valves.SEARCH_TIMEOUT_S, capture=True)
        return json.dumps({
            "success": result["success"],
            "output": (result.get("stdout") or "")[:4000],
            "stderr": result.get("stderr", ""),
        })

    async def knowledge_search(
        self,
        query: str,
        collection: str = "",
        top_k: int = 5,
        __user__: Optional[dict] = None,
    ) -> str:
        """Search OWUI's RAG knowledge collections for chunks
        relevant to `query`. Use this when the operator references
        prior conversations, the MiOS documentation, project notes,
        or any context that lives in a knowledge collection. Hits
        come back with {score, source, snippet} so the model can
        cite the source in its reply.

        Unlike OWUI's automatic pre-LLM RAG (which fires once per
        turn against the user prompt verbatim), this verb lets the
        agent issue ADDITIONAL queries with refined wording mid-
        tool-loop -- so a multi-step reasoning chain can pull
        targeted context after running other tools first (e.g.
        list installed games via mios_apps, then knowledge_search
        for the operator's prior notes about those games).

        :param query: The natural-language query string. Embedded
            via OWUI's configured embedding model.
        :param collection: Collection name OR id to scope the
            search. Empty = search all known collections.
        :param top_k: Number of chunks to return (1-20, default 5).
        :return: JSON string with {ok, query, collection,
            collection_id, hits:[{score, source, snippet}, ...]}.
        """
        if not self.valves.ENABLED:
            return json.dumps({"success": False, "stderr": "MiOS verbs disabled by valve"})
        if not query.strip():
            return json.dumps({"success": False, "stderr": "query is required"})
        top_k = max(1, min(20, int(top_k)))
        cmd = (f"mios-knowledge-search --json "
               f"--top-k {top_k} "
               f"{shlex.quote(query)}")
        if collection.strip():
            cmd += f" --collection {shlex.quote(collection.strip())}"
        result = _broker_send(cmd, timeout=self.valves.SEARCH_TIMEOUT_S, capture=True)
        return (result.get("stdout") or "").strip() or json.dumps({
            "success": False,
            "stderr": (result.get("stderr") or "")[:400],
        })

    async def directory_lookup(
        self,
        query: str,
        root: str = "",
        ext: str = "",
        kind: str = "",
        limit: int = 20,
        __user__: Optional[dict] = None,
    ) -> str:
        """Query the cached filesystem map mios-daemon maintains in
        Postgres + pgvector. Sub-100ms typical (DB query) vs the 60ms+ live
        mios-find / fs_search. The map is rebuilt every 15 min from
        operator-configured roots (vendor MiOS dirs + operator
        home + Windows mounts) and carries one-line summaries for
        text-shaped files so the model can rank relevance before
        opening each hit.

        Use this FIRST when the operator asks about a file or
        directory anywhere in the system. Fall back to mios-find
        or everything_search only when this returns no hits.

        :param query: Case-insensitive substring match against
            basename + path + summary.
        :param root: Filter to one root label (e.g. "mios-vendor",
            "operator-home", "windows-home"). Empty = search all.
        :param ext: File-extension filter (".md", ".toml", ...).
        :param kind: "file" | "dir" | "symlink" -- empty = any.
        :param limit: Max hits to return (default 20).
        :return: JSON {ok, query, filters, hits: [{path, kind,
            size, mtime, ext, summary, root_label}, ...]}.
        """
        if not self.valves.ENABLED:
            return json.dumps({"success": False, "stderr": "MiOS verbs disabled by valve"})
        if not query.strip():
            return json.dumps({"success": False, "stderr": "query is required"})
        cmd = f"mios-directory-lookup --json --limit {int(limit)} {shlex.quote(query)}"
        if root.strip():
            cmd += f" --root {shlex.quote(root.strip())}"
        if ext.strip():
            cmd += f" --ext {shlex.quote(ext.strip())}"
        if kind.strip():
            cmd += f" --kind {shlex.quote(kind.strip())}"
        result = _broker_send(cmd, timeout=self.valves.SEARCH_TIMEOUT_S, capture=True)
        return (result.get("stdout") or "").strip() or json.dumps({
            "success": False,
            "stderr": (result.get("stderr") or "")[:400],
        })

    async def os_recipe(
        self,
        name: str,
        params: Optional[dict] = None,
        os: str = "",
        __user__: Optional[dict] = None,
    ) -> str:
        """Run a NAMED, allow-listed OS shell recipe declared in
        mios.toml [recipes.*]. Picks the OS-appropriate template
        (linux / windows), shell-escapes every param, optionally
        converts Linux paths to Windows paths via wslpath, and
        dispatches via the operator-side broker.

        SSOT: every runnable recipe lives in mios.toml -- adding a
        new shell verb is a TOML edit, no code change. Hardening:
            * Only declared recipes execute (no arbitrary shell).
            * Only declared `args` survive (unknown kwargs dropped).
            * permission="write" recipes require MIOS_OS_RECIPE_WRITE=1.

        :param name: Recipe key (e.g. "open-folder", "lock-screen",
            "open-shell-folder", "show-network", "reveal-in-folder",
            "copy-to-clipboard", "show-process", "toast", "reboot").
            Use `mios-os-recipe list` from a shell to enumerate.
        :param params: Dict of {arg: value} matching the recipe's
            declared `args` list. Unknown keys are dropped.
        :param os: Override OS template: "linux" | "windows". Empty
            = WSL-aware detection (Linux by default; explicit
            "windows" forces explorer.exe / powershell.exe path).
        :return: JSON {success, recipe, target_os, template,
            exit_code, stdout, stderr}.
        """
        if not self.valves.ENABLED:
            return json.dumps({"success": False, "stderr": "MiOS verbs disabled by valve"})
        if not name.strip():
            return json.dumps({"success": False, "stderr": "recipe name is required"})
        params = params or {}
        kv = " ".join(
            f"{shlex.quote(str(k))}={shlex.quote(str(v))}"
            for k, v in params.items()
        )
        os_flag = ""
        if os and os.strip().lower() in ("linux", "windows"):
            os_flag = f"--os {shlex.quote(os.strip().lower())} "
        cmd = f"mios-os-recipe --json {os_flag}{shlex.quote(name)} {kv}".strip()
        result = _broker_send(cmd, timeout=self.valves.LAUNCH_TIMEOUT_S, capture=True)
        raw = (result.get("stdout") or "").strip()
        try:
            return json.dumps(json.loads(raw))
        except (json.JSONDecodeError, ValueError):
            return json.dumps({
                "success": result.get("success", False),
                "recipe": name,
                "stderr": result.get("stderr", "")[:600] or f"non-JSON: {raw[:300]}",
            })

    async def system_status(self, __user__: Optional[dict] = None) -> str:
        """Return a structured snapshot of the live MiOS host: GPU(s) +
        VRAM, RAM, disk, failed/active service count, MiOS service
        health roll-up (hermes / OWUI / prefilter / hermes-tail /
        llm_light), and the full model list. ONE tool call,
        deterministic JSON, no fabrication.

        Use this when the operator asks "system status", "dashboard",
        "what's running", "what GPU do I have", "how much disk left",
        "list models", or any system-overview question. Beats
        running df / nvidia-smi / systemctl / mios-llm-light models separately
        and assembling them in prose -- the model has been observed
        fabricating GPU fields ("No NVIDIA driver detected") when
        forced to do the assembly itself.

        :return: JSON string with {ts, uptime_s, gpu[], memory{},
            disk[], services{failed[], active_count, mios{}},
            models[]}. Missing probes come back as null or empty
            list -- never fabricated.
        """
        if not self.valves.ENABLED:
            return json.dumps({"success": False, "stderr": "MiOS verbs disabled by valve"})
        result = _broker_send("mios-system-status",
                              timeout=self.valves.INVENTORY_TIMEOUT_S,
                              capture=True)
        raw = result.get("stdout") or ""
        try:
            parsed = json.loads(raw)
            parsed["success"] = True
            return json.dumps(parsed)
        except (json.JSONDecodeError, ValueError):
            return json.dumps({
                "success": False,
                "stderr": result.get("stderr", "") or f"non-JSON from mios-system-status: {raw[:300]}",
            })

    async def open_app(
        self,
        name: str,
        position: PositionLiteral = "default",
        args: Optional[list[str]] = None,
        monitor: int = 0,
        __user__: Optional[dict] = None,
    ) -> str:
        """Open a desktop app on the operator's screen, optionally
        placing the window. Works for Windows apps (Steam/Epic/Xbox/
        protocol-handlers/.exe) and Linux GUI apps (any /usr/bin/<bin>
        via WSLg). Use this instead of `launch_app` when you ALSO
        want to control the window position; use `launch_app` when
        the operator only asked to launch (no placement).

        :param name: App name or substring (case-insensitive). Resolved
            through the canonical launch chain (Windows games inventory
            / start menu / MiOS shim / Linux PATH).
        :param position: Where to place the new window on the chosen
            monitor. 'as-is' = leave wherever the OS spawned it
            (no MoveWindow call). 'maximize' = full WorkingArea.
        :param args: Extra positional arguments (e.g. file path for
            `notepad`). Empty list / None = no extra args.
        :param monitor: 0-indexed monitor to target (0 = primary).
            Out-of-range values fall back to primary.
        :return: JSON {success, target, broker_output, stderr}.
        """
        if not self.valves.ENABLED:
            return json.dumps({"success": False, "stderr": "MiOS verbs disabled by valve"})
        env_prefix = ""
        if position and position != "as-is":
            env_prefix = f"MIOS_LAUNCH_POSITION={shlex.quote(position)} "
        if args:
            extra = " ".join(shlex.quote(a) for a in args)
            cmd = f"{env_prefix}mios-windows launch {shlex.quote(name)} {extra}"
        else:
            cmd = f"{env_prefix}mios-find {shlex.quote(name)} | bash"
        result = _broker_send(cmd, timeout=self.valves.LAUNCH_TIMEOUT_S, capture=True)
        target = ""
        for line in (result.get("stdout") or "").splitlines():
            if "launched" in line.lower() or " -> " in line:
                target = line.strip()[:300]
                break
        return json.dumps({
            "success": result["success"],
            "target": target,
            "broker_output": (result.get("stdout") or "")[:600],
            "stderr": result.get("stderr", ""),
        })

    async def focus_window(
        self,
        title: str,
        __user__: Optional[dict] = None,
    ) -> str:
        """Bring an existing window to the foreground by title pattern.
        Substring + case-insensitive match.

        :param title: Title pattern. First match wins.
        :return: JSON {success, hwnd, title, stderr}.
        """
        if not self.valves.ENABLED:
            return json.dumps({"success": False, "stderr": "disabled"})
        cmd = f"mios-window focus {shlex.quote(title)}"
        result = _broker_send(cmd, timeout=self.valves.SEARCH_TIMEOUT_S, capture=True)
        return json.dumps({
            "success": result["success"],
            "output": (result.get("stdout") or "")[:500],
            "stderr": result.get("stderr", ""),
        })

    async def move_window(
        self,
        title: str,
        position: PositionLiteral,
        monitor: int = 0,
        __user__: Optional[dict] = None,
    ) -> str:
        """Move an existing window to a canonical position on the
        chosen monitor. Substring + case-insensitive title match.

        :param title: Title pattern of the window to move.
        :param position: Canonical position enum (center/left/right/
            top/bottom/top-left/top-right/bottom-left/bottom-right/
            maximize). 'as-is' is a noop for this verb.
        :param monitor: 0-indexed monitor (0 = primary).
        :return: JSON {success, hwnd, applied_position, stderr}.
        """
        if not self.valves.ENABLED:
            return json.dumps({"success": False, "stderr": "disabled"})
        if position == "as-is":
            return json.dumps({"success": True, "applied_position": "as-is",
                               "stderr": "noop"})
        cmd = f"mios-window {shlex.quote(position)} {shlex.quote(title)}"
        result = _broker_send(cmd, timeout=self.valves.SEARCH_TIMEOUT_S, capture=True)
        return json.dumps({
            "success": result["success"],
            "applied_position": position,
            "output": (result.get("stdout") or "")[:500],
            "stderr": result.get("stderr", ""),
        })

    async def close_window(
        self,
        title: str,
        mode: CloseModeLiteral = "graceful",
        __user__: Optional[dict] = None,
    ) -> str:
        """Close a window by title pattern. Graceful (WM_CLOSE) lets
        the app save state; force (TerminateProcess) drops unsaved
        work and bypasses any save-prompt. NEVER use this on MiOS
        infrastructure (hermes-agent / mios-open-webui / mios-llm-light /
        mios-daemon / mios-launcher) -- those drop the conversation.
        Use `mios-restart <svc>` for graceful service restarts.

        :param title: Window title pattern. First match wins.
        :param mode: 'graceful' (WM_CLOSE; default) or 'force'
            (TerminateProcess; only when graceful failed).
        :return: JSON {success, mode, output, stderr}.
        """
        if not self.valves.ENABLED:
            return json.dumps({"success": False, "stderr": "disabled"})
        subcmd = "close" if mode == "graceful" else "kill"
        cmd = f"mios-window {subcmd} {shlex.quote(title)}"
        result = _broker_send(cmd, timeout=self.valves.SEARCH_TIMEOUT_S, capture=True)
        return json.dumps({
            "success": result["success"],
            "mode": mode,
            "output": (result.get("stdout") or "")[:500],
            "stderr": result.get("stderr", ""),
        })

    async def list_windows(
        self,
        __user__: Optional[dict] = None,
    ) -> str:
        """Enumerate visible top-level windows on the operator's
        desktop. Use this to disambiguate when the operator's title
        pattern matches multiple windows, or to verify a window is
        actually presented.

        :return: JSON {success, windows: [{hwnd, title, pid,
            monitor, visible}], stderr}.
        """
        if not self.valves.ENABLED:
            return json.dumps({"success": False, "stderr": "disabled"})
        result = _broker_send("mios-pc-control window-list",
                              timeout=self.valves.SEARCH_TIMEOUT_S, capture=True)
        raw = (result.get("stdout") or "").strip()
        try:
            parsed = json.loads(raw)
            return json.dumps({"success": True, "windows": parsed})
        except (json.JSONDecodeError, ValueError):
            return json.dumps({
                "success": result["success"],
                "output": raw[:2000],
                "stderr": result.get("stderr", ""),
            })

    async def screen_layout(
        self,
        __user__: Optional[dict] = None,
    ) -> str:
        """Report the operator's monitor layout so the model can
        reason about which monitor is which when positioning windows.

        :return: JSON {success, monitors: [{index, width, height,
            primary, scale}], primary_index, stderr}.
        """
        if not self.valves.ENABLED:
            return json.dumps({"success": False, "stderr": "disabled"})
        result = _broker_send("mios-pc-control screen-layout",
                              timeout=self.valves.SEARCH_TIMEOUT_S, capture=True)
        raw = (result.get("stdout") or "").strip()
        try:
            parsed = json.loads(raw)
            return json.dumps({"success": True, **parsed})
        except (json.JSONDecodeError, ValueError):
            return json.dumps({
                "success": result["success"],
                "output": raw[:1000],
                "stderr": result.get("stderr", ""),
            })

    async def open_url(
        self,
        url: str,
        browser: Optional[str] = None,
        __user__: Optional[dict] = None,
    ) -> str:
        """Open a URL in the operator's MiOS-defined default browser
        (visible). NEVER use `browser_navigate` for operator-visible
        browsing -- that drives a headless CDP session the operator
        can't see.

        :param url: Absolute URL to open (http/https/file/etc).
        :param browser: Override the default browser name (e.g.
            'chromedev'). None = the SSOT default browser,
            mios.toml [aliases].browser (MiOS-Portal).
        :return: JSON {success, url, browser, stderr}.
        """
        if not self.valves.ENABLED:
            return json.dumps({"success": False, "stderr": "disabled"})
        cmd = f"mios-open-url {shlex.quote(url)}"
        if browser:
            cmd += f" {shlex.quote(browser)}"
        result = _broker_send(cmd, timeout=self.valves.LAUNCH_TIMEOUT_S, capture=True)
        return json.dumps({
            "success": result["success"],
            "url": url,
            "browser": browser or "(default)",
            "stderr": result.get("stderr", ""),
        })

    async def service_status(self, name: str, __user__: Optional[dict] = None) -> str:
        """systemctl status snapshot for a Linux service. Read-only.
        Returns is-active + first 20 lines of status output.
        """
        if not self.valves.ENABLED:
            return json.dumps({"success": False, "stderr": "disabled"})
        q = shlex.quote(name)
        cmd = (
            f"echo '=== is-active ==='; systemctl is-active {q}; "
            f"echo; echo '=== status ==='; "
            f"systemctl --no-pager status {q} | head -20"
        )
        result = _broker_send(cmd, timeout=self.valves.SEARCH_TIMEOUT_S, capture=True)
        return json.dumps({
            "success": result["success"],
            "name": name,
            "output": (result.get("stdout") or "")[:4000],
            "stderr": result.get("stderr", ""),
        })

    async def service_restart(self, name: str, __user__: Optional[dict] = None) -> str:
        """systemctl restart <name>. WRITE verb -- visible side effect.
        Confirms via post-restart is-active line.
        """
        if not self.valves.ENABLED:
            return json.dumps({"success": False, "stderr": "disabled"})
        q = shlex.quote(name)
        cmd = (
            f"systemctl restart {q} && "
            f"echo \"restarted; is-active=$(systemctl is-active {q})\""
        )
        result = _broker_send(cmd, timeout=self.valves.LAUNCH_TIMEOUT_S, capture=True)
        return json.dumps({
            "success": result["success"],
            "name": name,
            "output": (result.get("stdout") or "")[:1000],
            "stderr": result.get("stderr", ""),
        })

    async def process_list(
        self,
        filter: str = "",
        sort: str = "rss",
        limit: int = 20,
        __user__: Optional[dict] = None,
    ) -> str:
        """ps snapshot, sorted by rss (default) or cpu. Read-only.

        :param filter: case-insensitive substring on command name.
        :param sort: "rss" (memory, default) or "cpu".
        :param limit: max lines (default 20).
        """
        if not self.valves.ENABLED:
            return json.dumps({"success": False, "stderr": "disabled"})
        sort_arg = "--sort=-pcpu" if sort.lower() == "cpu" else "--sort=-rss"
        base = f"ps -eo pid,user,rss,pcpu,comm,args {sort_arg} --no-headers"
        if filter.strip():
            base += f" | grep -i -- {shlex.quote(filter.strip())}"
        cmd = f"{base} | head -{int(limit)}"
        result = _broker_send(cmd, timeout=self.valves.SEARCH_TIMEOUT_S, capture=True)
        lines = [ln for ln in (result.get("stdout") or "").splitlines() if ln.strip()]
        return json.dumps({
            "success": result["success"],
            "lines": lines,
            "count": len(lines),
            "stderr": result.get("stderr", ""),
        })

    async def container_status(self, name: str = "", __user__: Optional[dict] = None) -> str:
        """podman ps -a snapshot. Read-only.

        :param name: optional case-insensitive substring filter on container name.
        """
        if not self.valves.ENABLED:
            return json.dumps({"success": False, "stderr": "disabled"})
        base = "podman ps -a --format '{{.Names}}\\t{{.Status}}\\t{{.Image}}'"
        if name.strip():
            base += f" | grep -i -- {shlex.quote(name.strip())}"
        result = _broker_send(base, timeout=self.valves.SEARCH_TIMEOUT_S, capture=True)
        return json.dumps({
            "success": result["success"],
            "output": (result.get("stdout") or "")[:4000],
            "stderr": result.get("stderr", ""),
        })

    async def container_restart(self, name: str, __user__: Optional[dict] = None) -> str:
        """podman restart <name>. WRITE verb.

        :param name: container name (exact or substring -- podman resolves).
        """
        if not self.valves.ENABLED:
            return json.dumps({"success": False, "stderr": "disabled"})
        q = shlex.quote(name)
        cmd = (
            f"podman restart {q} && "
            f"podman ps --filter name={q} --format '{{{{.Names}}}}\\t{{{{.Status}}}}'"
        )
        result = _broker_send(cmd, timeout=self.valves.LAUNCH_TIMEOUT_S, capture=True)
        return json.dumps({
            "success": result["success"],
            "name": name,
            "output": (result.get("stdout") or "")[:1000],
            "stderr": result.get("stderr", ""),
        })
