# AI-hint: Tests for task_677: mios_microvm — virtio-pmem DAX memory storage manager.
# AI-related: usr/libexec/mios/virt/mios_microvm.py
# AI-functions: test_launch_dry_run_creates_entry, test_destroy_releases_entry

"""Tests for task_677: mios_microvm — virtio-pmem DAX memory storage manager."""
import sys, os, json, types, pathlib
sys.path.insert(0, "usr/libexec/mios/virt")

from mios_microvm import MicroVM, _VMS

def test_launch_dry_run_creates_entry():
    """Dry-run launch registers the VM and allocates a stub memfd."""
    _VMS.clear()
    vm   = MicroVM("test-vm-001", rootfs="/nonexistent.raw",
                   memory_mb=512, cpus=1)
    info = vm.launch(dry_run=True)
    assert info["vm_id"] == "test-vm-001"
    assert info["status"] == "running"
    assert "test-vm-001" in _VMS
    # Cleanup
    try:
        os.close(info["memfd"])
    except OSError:
        pass

def test_destroy_releases_entry():
    """destroy() removes the VM from the registry."""
    _VMS.clear()
    vm   = MicroVM("test-vm-002", rootfs="/nonexistent.raw")
    info = vm.launch(dry_run=True)
    vm.destroy()
    assert "test-vm-002" not in _VMS
    # memfd fd should be closed (double-close raises OSError)
    # already closed by destroy(), so just verify no entry remains
    assert len([v for v in _VMS if v.startswith("test-vm-002")]) == 0

if __name__ == "__main__":
    test_launch_dry_run_creates_entry()
    test_destroy_releases_entry()
    print("All task_677 tests passed.")
