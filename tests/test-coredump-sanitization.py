# AI-hint: Tests for task_695 & task_696: crash minidump extraction and raw core purge.
# AI-related: mios-node
# AI-functions: test_minidump_extraction_and_purge

"""Tests for task_695 & task_696: crash minidump extraction and raw core purge."""
import sys
sys.path.insert(0, "usr/libexec/mios/diag")
from coredump_sanitizer import CoredumpSanitizer

def test_minidump_extraction_and_purge():
    """Verify segfaults produce <1MB sanitized minidumps and purge raw cores."""
    sanitizer = CoredumpSanitizer()
    fake_core = b"CANARY_SECRET_DATA" + bytes(1024 * 50)

    minidump = sanitizer.process_crash("mios-node", 1337, fake_core)
    assert minidump.sanitized
    assert minidump.size_kb < 1024 # < 1MB
    assert len(minidump.stack_trace) > 0
    assert sanitizer.raw_cores_on_disk == 0, "Raw core file must be purged from disk"

if __name__ == "__main__":
    test_minidump_extraction_and_purge()
    print("All task_695/task_696 tests passed.")
