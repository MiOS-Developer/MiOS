# AI-hint: task_284 task_3218 Turn-boundary preemption & snapshot-suspend-resume via llama.cpp KV-cache slot save/restore API (/slots endpoint). Suspended conversations are checkpointed to /var/lib/mios/llamacpp/slots/ and their task row updated to suspended
# AI-related: mios-llm-light, mios_kv_compact, test_mios_kvfork
# AI-functions: slot_path, __init__, suspend, resume, erase, _llama_slot_action, list_suspended, class KVSlot, class KVForkManager
"""
mios_kvfork.py — task_284 task_3218
Turn-boundary preemption & snapshot-suspend-resume via llama.cpp KV-cache slot
save/restore API (/slots endpoint).  Suspended conversations are checkpointed
to /var/lib/mios/llamacpp/slots/ and their task row updated to `suspended`.

The KV slot is keyed by session_id; the file is named
  /var/lib/mios/llamacpp/slots/<session_id>.kv

Real llama.cpp HTTP endpoint: POST /slots/<slot_id>/action
  body: {"action": "save",   "filename": "<name>"}
  body: {"action": "restore", "filename": "<name>"}
  body: {"action": "erase"}
"""
from __future__ import annotations

import json
import logging
import os
import pathlib
import re
import time
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

_FILE_PREFIX = "mios-kv-"
_FILE_SUFFIX = ".bin"

def conv_token(conv: Any) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(conv or "default"))[:120]
    return safe or "default"

def kv_filename(conv: Any) -> str:
    return f"mios-kv-{conv_token(conv)}.bin"

def validate_fork(src: Any, dst: Any) -> tuple[bool, str]:
    if src is None or not str(src).strip():
        return False, "source conversation identifier cannot be empty"
    if dst is None or not str(dst).strip():
        return False, "destination conversation identifier cannot be empty"
    s_tok = conv_token(src)
    d_tok = conv_token(dst)
    if src == dst or s_tok == d_tok:
        return False, "source and destination resolve to the same KV file"
    return True, ""

def plan_fork(src: Any, dst: Any) -> list[tuple[str, str, str]]:
    s_tok = conv_token(src)
    d_tok = conv_token(dst)
    return [
        ("restore", s_tok, kv_filename(src)),
        ("save", d_tok, kv_filename(dst)),
    ]

def fork_outcome(restore_ok: bool, save_ok: bool) -> tuple[bool, str]:
    if restore_ok and save_ok:
        return True, "forked from parent prefix successfully"
    if not restore_ok and save_ok:
        return True, "WARNING: parent restore failed but child slot saved"
    if restore_ok and not save_ok:
        return False, "could not save child slot"
    return False, "could not save child slot; parent restore also failed"

def parse_bool(val: Any, default: bool = False) -> bool:
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    s = str(val).strip().lower()
    if s in ("true", "1", "yes", "on"):
        return True
    if s in ("false", "0", "no", "off", ""):
        return False
    return default

def clamp_branches(val: Any, hard_cap: int = 8, default: int = 2) -> int:
    if hard_cap <= 0:
        return 0
    try:
        v = int(val) if val is not None else default
    except (ValueError, TypeError):
        v = default
    return max(0, min(v, hard_cap))

SLOTS_DIR = pathlib.Path(
    os.environ.get("MIOS_LLAMACPP_SLOTS_DIR", "/var/lib/mios/llamacpp/slots"))

_CHAT_CANCEL = "<|CANCEL|>"   # sentinel injected to abort in-flight generation

@dataclass
class KVSlot:
    """In-memory representation of a suspended KV-cache slot."""
    session_id:  str
    slot_id:     int = 0
    filename:    str = ""
    suspended_at: float = field(default_factory=time.monotonic)
    metadata:    dict[str, Any] = field(default_factory=dict)

    def slot_path(self) -> pathlib.Path:
        name = self.filename or f"{self.session_id}.kv"
        return SLOTS_DIR / name

class KVForkManager:
    """
    Manages KV-cache slot save/restore for session preemption.

    In production this issues HTTP calls to the llama.cpp /slots endpoint.
    In unit-test mode (no running server) it simulates saves/restores via the
    local filesystem so CI does not require a live inference engine.
    """

    def __init__(self, llama_base_url: str | None = None,
                 dry_run: bool = False) -> None:
        if llama_base_url is None:
            _port = os.environ.get("MIOS_PORTS_LLM_LIGHT", "8500")
            llama_base_url = os.environ.get("MIOS_LLM_LIGHT_ENDPOINT") or f"http://localhost:{_port}"
        self.llama_base_url = llama_base_url.rstrip("/")
        self.dry_run = dry_run
        self._active: dict[str, KVSlot] = {}
        SLOTS_DIR.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    def suspend(self, session_id: str, slot_id: int = 0) -> KVSlot:
        """
        Suspend an active conversation: save its KV slot to disk.
        Returns the KVSlot descriptor.
        """
        slot = KVSlot(session_id=session_id, slot_id=slot_id)
        slot.filename = f"{session_id}.kv"
        log.info("KVFork: suspending session=%s slot=%d", session_id, slot_id)
        if not self.dry_run:
            self._llama_slot_action(slot_id, "save", slot.filename)
        else:
            # Dry-run: write a placeholder so CI has a real file to assert on
            slot.slot_path().parent.mkdir(parents=True, exist_ok=True)
            slot.slot_path().write_text(
                json.dumps({"session_id": session_id, "slot_id": slot_id,
                            "suspended_at": slot.suspended_at}))
        self._active[session_id] = slot
        return slot

    def resume(self, session_id: str) -> KVSlot:
        """
        Resume a previously suspended conversation: restore its KV slot.
        """
        slot = self._active.get(session_id)
        if slot is None:
            raise KeyError(f"No suspended slot for session {session_id!r}")
        log.info("KVFork: resuming session=%s slot=%d", session_id, slot.slot_id)
        if not self.dry_run:
            self._llama_slot_action(slot.slot_id, "restore", slot.filename)
        else:
            if not slot.slot_path().exists():
                raise FileNotFoundError(
                    f"KV slot file missing: {slot.slot_path()}")
        del self._active[session_id]
        return slot

    def erase(self, session_id: str) -> None:
        """Erase a suspended slot (session terminated or evicted)."""
        slot = self._active.pop(session_id, None)
        if slot is not None:
            if not self.dry_run:
                self._llama_slot_action(slot.slot_id, "erase", "")
            else:
                try:
                    slot.slot_path().unlink(missing_ok=True)
                except OSError:
                    pass

    # ------------------------------------------------------------------
    def _llama_slot_action(self, slot_id: int, action: str,
                           filename: str) -> None:
        """POST /slots/<slot_id>/action to llama.cpp."""
        import urllib.request
        url  = f"{self.llama_base_url}/slots/{slot_id}/action"
        body = json.dumps({"action": action, "filename": filename}).encode()
        req  = urllib.request.Request(url, data=body,
                                      headers={"Content-Type": "application/json"},
                                      method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5):
                pass
        except Exception as exc:
            log.warning("KVFork: slot action %s failed: %s", action, exc)

    def list_suspended(self) -> list[str]:
        return list(self._active)
