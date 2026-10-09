# AI-hint: Tests for task_284: mios_kvfork — KV snapshot-suspend-resume.
# AI-related: mios_kvfork
# AI-functions: test_suspend_creates_slot_file, test_resume_removes_from_active, test_erase_cleans_up_file

"""Tests for task_284: mios_kvfork — KV snapshot-suspend-resume."""
import sys, pathlib, tempfile, os
sys.path.insert(0, "usr/lib/mios/agent-pipe")

# Override slots dir to a temp directory for CI isolation
import tempfile
_TMP_SLOTS = tempfile.mkdtemp(prefix="mios_kvfork_test_")
os.environ["MIOS_LLAMACPP_SLOTS_DIR"] = _TMP_SLOTS

import importlib
import mios_kvfork
importlib.reload(mios_kvfork)
from mios_kvfork import KVForkManager

def test_suspend_creates_slot_file():
    """suspend() writes a KV slot file to disk in dry_run mode."""
    mgr = KVForkManager(dry_run=True)
    slot = mgr.suspend("sess-001", slot_id=0)
    assert slot.slot_path().exists(), "KV slot file not created"
    import json
    data = json.loads(slot.slot_path().read_text())
    assert data["session_id"] == "sess-001"

def test_resume_removes_from_active():
    """resume() restores the slot and removes it from the active map."""
    mgr = KVForkManager(dry_run=True)
    mgr.suspend("sess-002", slot_id=1)
    assert "sess-002" in mgr.list_suspended()
    slot = mgr.resume("sess-002")
    assert slot.session_id == "sess-002"
    assert "sess-002" not in mgr.list_suspended()

def test_erase_cleans_up_file():
    """erase() deletes the checkpoint file in dry_run mode."""
    mgr = KVForkManager(dry_run=True)
    slot = mgr.suspend("sess-003", slot_id=2)
    path = slot.slot_path()
    assert path.exists()
    mgr.erase("sess-003")
    assert not path.exists()

if __name__ == "__main__":
    try:
        test_suspend_creates_slot_file()
        test_resume_removes_from_active()
        test_erase_cleans_up_file()
        print("All task_284 tests passed.")
    finally:
        import shutil
        shutil.rmtree(_TMP_SLOTS, ignore_errors=True)
