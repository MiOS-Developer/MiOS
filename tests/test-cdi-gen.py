#!/usr/bin/env python3
# AI-hint: Private hardware and official-toolkit boundary controls for hardware-backed CDI generation.
# AI-doc: usr/share/doc/mios/manual/ch20-container-device-interface-plumbing.md
# AI-related: usr/libexec/mios/mios-cdi-gen, tests/test-cdi-device-isolate.sh
from __future__ import annotations

import builtins
import contextlib
import glob
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

_ROOT = Path(__file__).resolve().parent.parent
_CDI_GEN = os.environ.get("MIOS_CDI_GEN_UNDER_TEST", str(_ROOT / "usr/libexec/mios/mios-cdi-gen"))


def load_generator():
    loader = importlib.machinery.SourceFileLoader("mios_cdi_private", _CDI_GEN)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class Hardware:
    """Map kernel filesystem reads into private files; no host device mutation."""

    def __init__(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mios-cdi-hardware-")
        self.root = Path(self.temp.name)
        self.module = load_generator()
        self.characters = set()
        self.output = self.root / "output"
        self.original_open = builtins.open
        self.original_glob = glob.glob
        self.original_exists = os.path.exists
        self.original_isfile = os.path.isfile
        self.original_islink = os.path.islink
        self.original_readlink = os.readlink
        self.stack = contextlib.ExitStack()
        self.toolkit = "/usr/bin/nvidia-ctk"
        self.tool_status = 0
        self.tool_error = ""
        self.tool_output = None
        self.after_generate = None
        self.hook = self.root / "driver/nvidia-cdi-hook"
        self.hook.parent.mkdir()
        self.hook.write_text("#!/bin/sh\nexit 0\n")
        self.hook.chmod(0o755)
        self.library = self.root / "driver/libcuda.so"
        self.library.write_bytes(b"private driver library")

    def path(self, path):
        path = os.fspath(path) if isinstance(path, os.PathLike) else path
        if isinstance(path, str) and path.startswith(("/dev/", "/sys/", "/proc/")):
            return str(self.root / path.lstrip("/"))
        return path

    def write(self, path, text=""):
        target = Path(self.path(path))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        return target

    def character(self, path):
        self.write(path)
        self.characters.add(path)

    def driver(self, device, name):
        target = Path(self.path(device + "/driver"))
        if target.is_symlink():
            target.unlink()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to("/sys/bus/pci/drivers/" + name)

    def nvidia(self, address="0000:01:00.0", minor=0, driver="nvidia"):
        pci = "/sys/bus/pci/devices/" + address
        self.write(pci + "/vendor", "0x10de")
        self.write(pci + "/class", "0x030000")
        self.driver(pci, driver)
        self.write("/proc/driver/nvidia/gpus/" + address + "/information", f"Device Minor: {minor}\n")
        for path in (f"/dev/nvidia{minor}", "/dev/nvidiactl", "/dev/nvidia-uvm"):
            self.character(path)

    def dri(self, index=128, vendor="0x1002", driver="amdgpu"):
        render = "/dev/dri/renderD" + str(index)
        card = "/dev/dri/card" + str(index - 128)
        pci = "/sys/class/drm/" + Path(render).name + "/device"
        self.character(render)
        self.character(card)
        self.write(pci + "/vendor", vendor)
        self.write(pci + "/drm/" + Path(card).name)
        self.driver(pci, driver)

    def official_spec(self):
        paths = sorted(path for path in self.characters
                       if path.startswith("/dev/nvidia") and path[11:].isdigit())
        devices = [{"name": Path(path).name[6:], "containerEdits": {"deviceNodes": [{"path": path}]}}
                   for path in paths]
        if "/dev/dxg" in self.characters and not paths:
            devices = [{"name": "0", "containerEdits": {"deviceNodes": [{"path": "/dev/dxg"}]}}]
        if devices:
            devices.append({"name": "all", "containerEdits": {
                "deviceNodes": [node for device in devices for node in device["containerEdits"]["deviceNodes"]]}})
        return {
            "cdiVersion": "0.8.0", "kind": "nvidia.com/gpu",
            "annotations": {"fixture": "official-toolkit-output"},
            "devices": devices,
            "containerEdits": {
                "deviceNodes": [{"path": path} for path in ("/dev/nvidiactl", "/dev/nvidia-uvm")
                                if path in self.characters],
                "env": ["NVIDIA_VISIBLE_DEVICES=void"],
                "mounts": [{"hostPath": str(self.library), "containerPath": "/usr/lib64/libcuda.so",
                            "options": ["ro", "nosuid", "nodev", "bind"]}],
                "hooks": [{"hookName": "createContainer", "path": str(self.hook),
                           "args": ["nvidia-cdi-hook", "update-ldcache"]}],
            },
        }

    def generate(self, args, **kwargs):
        output = self.tool_output if self.tool_output is not None else json.dumps(self.official_spec())
        if self.after_generate:
            self.after_generate()
        return subprocess.CompletedProcess(args, self.tool_status, output, self.tool_error)

    def __enter__(self):
        def mapped_glob(pattern, *args, **kwargs):
            return [value.replace(str(self.root), "", 1) for value in
                    self.original_glob(self.path(pattern), *args, **kwargs)]
        self.stack.enter_context(mock.patch.object(glob, "glob", side_effect=mapped_glob))
        self.stack.enter_context(mock.patch.object(builtins, "open",
            side_effect=lambda path, *args, **kwargs: self.original_open(self.path(path), *args, **kwargs)))
        self.stack.enter_context(mock.patch.object(os.path, "exists",
            side_effect=lambda path: self.original_exists(self.path(path))))
        self.stack.enter_context(mock.patch.object(os.path, "isfile",
            side_effect=lambda path: self.original_isfile(self.path(path))))
        self.stack.enter_context(mock.patch.object(os.path, "islink",
            side_effect=lambda path: self.original_islink(self.path(path))))
        self.stack.enter_context(mock.patch.object(os, "readlink",
            side_effect=lambda path, *args, **kwargs: self.original_readlink(self.path(path), *args, **kwargs)))
        self.stack.enter_context(mock.patch.object(self.module, "_is_character_device",
            side_effect=lambda path: path in self.characters and self.original_exists(self.path(path)), create=True))
        self.stack.enter_context(mock.patch.object(shutil, "which", side_effect=lambda name: self.toolkit))
        self.runner = self.stack.enter_context(mock.patch.object(subprocess, "run", side_effect=self.generate))
        return self

    def __exit__(self, *args):
        self.stack.close()
        self.temp.cleanup()

    def cli(self, *args):
        output = io.StringIO()
        with mock.patch.object(sys, "argv", ["mios-cdi-gen", "--output-dir", str(self.output), "--json", *args]), \
                contextlib.redirect_stdout(output):
            status = self.module.main()
        return status, json.loads(output.getvalue())


class TestCdiGen(unittest.TestCase):
    def test_binary_exists_and_executable(self):
        self.assertTrue(Path(_CDI_GEN).is_file())
        self.assertTrue(os.access(_CDI_GEN, os.X_OK))

    def test_real_character_check_rejects_regular_files(self):
        module = load_generator()
        with tempfile.NamedTemporaryFile(prefix="mios-cdi-regular-") as file:
            self.assertFalse(module._is_character_device(file.name))
        self.assertTrue(module._is_character_device("/dev/null"))

    def test_no_hardware_means_no_specs_even_when_all_forced(self):
        with Hardware() as f:
            status, result = f.cli("--vendor", "all", "--dry-run")
            self.assertEqual(status, 0)
            self.assertEqual(result["generated"], [])
            self.assertEqual(result["specs"], {})
            self.assertFalse(f.output.exists())
            f.runner.assert_not_called()

    def test_forced_vendor_without_hardware_is_unavailable(self):
        for vendor in ("nvidia", "amd", "intel"):
            with self.subTest(vendor=vendor), Hardware() as f:
                status, result = f.cli("--vendor", vendor, "--dry-run")
                self.assertEqual(status, 1)
                self.assertEqual(result["status"], "unavailable")
                self.assertEqual(result["generated"], [])

    def test_empty_builders_do_not_fabricate_devices(self):
        with Hardware() as f:
            self.assertEqual(f.module.build_nvidia_cdi_spec({"instances": {}, "common": []})["devices"], [])
            self.assertEqual(f.module.build_amd_cdi_spec([])["devices"], [])
            self.assertEqual(f.module.build_intel_cdi_spec([])["devices"], [])

    def test_nvidia_requires_current_driver_binding(self):
        with Hardware() as f:
            f.nvidia(driver="vfio-pci")
            self.assertEqual(f.module.find_nvidia_devices()["instances"], {})

    def test_nvidia_rejects_regular_file_instead_of_device(self):
        with Hardware() as f:
            f.nvidia()
            f.characters.remove("/dev/nvidia0")
            self.assertEqual(f.module.find_nvidia_devices()["instances"], {})

    def test_nvidia_requires_driver_minor_evidence(self):
        with Hardware() as f:
            f.nvidia()
            f.write("/proc/driver/nvidia/gpus/0000:01:00.0/information", "unreadable minor")
            self.assertEqual(f.module.find_nvidia_devices()["instances"], {})

    def test_nvidia_sparse_minors_are_preserved(self):
        with Hardware() as f:
            f.nvidia(minor=3)
            devices = f.module.find_nvidia_devices()
            self.assertEqual(devices["instances"]["3"]["index"], 3)

    def test_nvidia_non_display_pci_function_is_not_a_gpu(self):
        with Hardware() as f:
            f.nvidia()
            f.write("/sys/bus/pci/devices/0000:01:00.0/class", "0x040300")
            self.assertEqual(f.module.find_nvidia_devices()["instances"], {})

    def test_dri_vendor_and_bound_driver_must_match(self):
        with Hardware() as f:
            f.dri()
            f.dri(index=129, vendor="0x8086", driver="i915")
            self.assertEqual([n["render"] for n in f.module.find_dri_devices("0x1002")], ["/dev/dri/renderD128"])
            self.assertEqual([n["render"] for n in f.module.find_dri_devices("0x8086")], ["/dev/dri/renderD129"])
            f.driver("/sys/class/drm/renderD128/device", "vfio-pci")
            self.assertEqual(f.module.find_dri_devices("0x1002"), [])

    def test_unknown_dri_vendor_is_not_any_requested_vendor(self):
        with Hardware() as f:
            f.dri(vendor="")
            self.assertEqual(f.module.find_dri_devices("0x1002"), [])
            self.assertEqual(f.module.find_dri_devices("0x8086"), [])

    def test_regular_dri_file_is_not_a_device(self):
        with Hardware() as f:
            f.dri()
            f.characters.remove("/dev/dri/renderD128")
            self.assertEqual(f.module.find_dri_devices("0x1002"), [])

    def test_amd_kfd_without_render_gpu_does_not_advertise_a_gpu(self):
        with Hardware() as f:
            f.character("/dev/kfd")
            status, result = f.cli("--vendor", "all", "--dry-run")
            self.assertEqual(status, 0)
            self.assertEqual(result["generated"], [])

    def test_dri_binding_change_after_probe_is_rejected(self):
        with Hardware() as f:
            f.dri()
            nodes = f.module.find_dri_devices("0x1002")
            f.driver("/sys/class/drm/renderD128/device", "vfio-pci")
            with self.assertRaises(RuntimeError):
                f.module.build_amd_cdi_spec(nodes)

    def test_official_nvidia_spec_is_preserved_including_mounts_hooks_aliases(self):
        with Hardware() as f:
            f.nvidia()
            expected = f.official_spec()
            actual = f.module.build_nvidia_cdi_spec(f.module.find_nvidia_devices())
            self.assertEqual(actual, expected)
            command = f.runner.call_args.args[0]
            self.assertIn("--output=", command)
            self.assertIn("--format=json", command)
            self.assertIn("--mode=nvml", command)
            self.assertIn("--device-id=all", command)

    def test_nvidia_requires_official_toolkit(self):
        with Hardware() as f:
            f.nvidia()
            f.toolkit = None
            with self.assertRaisesRegex(RuntimeError, "unavailable"):
                f.module.build_nvidia_cdi_spec(f.module.find_nvidia_devices())

    def test_toolkit_failure_has_no_handrolled_fallback(self):
        with Hardware() as f:
            f.nvidia()
            f.tool_status = 42
            f.tool_error = "driver/library mismatch"
            with self.assertRaisesRegex(RuntimeError, "driver/library mismatch"):
                f.module.build_nvidia_cdi_spec(f.module.find_nvidia_devices())

    def test_malformed_empty_or_wrong_kind_toolkit_spec_is_rejected(self):
        for value in ("bad JSON", "[]", '{"kind":"nvidia.com/gpu","devices":[]}', '{"kind":"intel.com/gpu"}'):
            with self.subTest(value=value), Hardware() as f:
                f.nvidia()
                f.tool_output = value
                with self.assertRaises(RuntimeError):
                    f.module.build_nvidia_cdi_spec(f.module.find_nvidia_devices())

    def test_device_nodes_without_driver_edits_are_rejected(self):
        with Hardware() as f:
            f.nvidia()
            spec = f.official_spec()
            del spec["containerEdits"]
            f.tool_output = json.dumps(spec)
            with self.assertRaisesRegex(RuntimeError, "mounts or runtime hooks"):
                f.module.build_nvidia_cdi_spec(f.module.find_nvidia_devices())

    def test_missing_mount_hook_or_node_is_rejected(self):
        for missing in ("library", "hook", "node"):
            with self.subTest(missing=missing), Hardware() as f:
                f.nvidia()
                spec = f.official_spec()
                f.tool_output = json.dumps(spec)
                if missing == "library":
                    f.library.unlink()
                elif missing == "hook":
                    f.hook.unlink()
                else:
                    spec["devices"][0]["containerEdits"]["deviceNodes"][0]["path"] = "/dev/nvidia99"
                    f.tool_output = json.dumps(spec)
                with self.assertRaises(RuntimeError):
                    f.module.build_nvidia_cdi_spec(f.module.find_nvidia_devices())

    def test_nvidia_binding_change_during_generation_is_rejected(self):
        with Hardware() as f:
            f.nvidia()
            f.after_generate = lambda: f.driver("/sys/bus/pci/devices/0000:01:00.0", "vfio-pci")
            with self.assertRaisesRegex(RuntimeError, "ownership changed"):
                f.module.build_nvidia_cdi_spec(f.module.find_nvidia_devices())

    def test_dry_run_does_not_create_directory_or_change_existing_spec(self):
        with Hardware() as f:
            f.nvidia()
            f.output.mkdir()
            existing = f.output / "nvidia.yaml"
            existing.write_text("previous receipt")
            status, result = f.cli("--vendor", "nvidia", "--dry-run")
            self.assertEqual(status, 0)
            self.assertEqual(existing.read_text(), "previous receipt")
            self.assertEqual(result["specs"]["nvidia"]["spec"], f.official_spec())

    def test_apply_writes_only_detected_vendors_with_readable_atomic_files(self):
        with Hardware() as f:
            f.dri()
            status, result = f.cli("--vendor", "all")
            self.assertEqual(status, 0)
            self.assertEqual(result["generated"], ["amd"])
            self.assertEqual(sorted(p.name for p in f.output.iterdir()), ["amd.json"])
            self.assertEqual((f.output / "amd.json").stat().st_mode & 0o777, 0o644)

    def test_detach_removes_only_own_stale_spec_and_dry_run_preserves_it(self):
        with Hardware() as f:
            f.output.mkdir()
            (f.output / "nvidia.yaml").write_text("stale")
            (f.output / "operator.json").write_text("operator")
            status, _ = f.cli("--vendor", "nvidia", "--dry-run")
            self.assertEqual(status, 1)
            self.assertTrue((f.output / "nvidia.yaml").exists())
            status, _ = f.cli("--vendor", "nvidia")
            self.assertEqual(status, 1)
            self.assertFalse((f.output / "nvidia.yaml").exists())
            self.assertEqual((f.output / "operator.json").read_text(), "operator")

    def test_toolkit_failure_cannot_leave_previous_nvidia_advertisement(self):
        with Hardware() as f:
            f.nvidia()
            f.output.mkdir()
            (f.output / "nvidia.yaml").write_text("stale")
            f.tool_status = 42
            status, result = f.cli("--vendor", "nvidia")
            self.assertEqual(status, 1)
            self.assertEqual(result["status"], "error")
            self.assertFalse((f.output / "nvidia.yaml").exists())

    def test_real_wsl_device_uses_official_wsl_discovery(self):
        with Hardware() as f:
            f.character("/dev/dxg")
            spec = f.module.build_nvidia_cdi_spec(f.module.find_nvidia_devices())
            self.assertEqual(spec, f.official_spec())
            self.assertIn("--mode=wsl", f.runner.call_args.args[0])

    def test_intel_xe_is_published_without_other_vendors(self):
        with Hardware() as f:
            f.dri(vendor="0x8086", driver="xe")
            status, result = f.cli("--vendor", "all")
            self.assertEqual(status, 0)
            self.assertEqual(result["generated"], ["intel"])
            self.assertEqual(sorted(p.name for p in f.output.iterdir()), ["intel.json"])

    def test_canonical_nvidia_output_retires_legacy_json(self):
        with Hardware() as f:
            f.nvidia()
            f.output.mkdir()
            (f.output / "nvidia.json").write_text("legacy")
            (f.output / "operator.yaml").write_text("keep")
            status, result = f.cli("--vendor", "nvidia")
            self.assertEqual(status, 0)
            self.assertEqual(Path(result["specs"]["nvidia"]["target"]).name, "nvidia.yaml")
            self.assertEqual(json.loads((f.output / "nvidia.yaml").read_text()), f.official_spec())
            self.assertFalse((f.output / "nvidia.json").exists())
            self.assertEqual((f.output / "operator.yaml").read_text(), "keep")

    def test_invalid_nested_edits_and_missing_control_nodes_fail_closed(self):
        for key in ("mounts", "hooks", "deviceNodes"):
            with self.subTest(key=key), Hardware() as f:
                f.nvidia()
                spec = f.official_spec()
                spec["containerEdits"][key] = None
                f.tool_output = json.dumps(spec)
                status, result = f.cli("--vendor", "nvidia", "--dry-run")
                self.assertEqual(status, 1)
                self.assertEqual(result["status"], "error")
        with Hardware() as f:
            f.nvidia()
            spec = f.official_spec()
            spec["containerEdits"]["deviceNodes"] = []
            f.tool_output = json.dumps(spec)
            with self.assertRaisesRegex(RuntimeError, "control device"):
                f.module.build_nvidia_cdi_spec(f.module.find_nvidia_devices())

    def test_linked_output_is_refused_without_modifying_referent(self):
        with Hardware() as f:
            f.dri()
            f.output.mkdir()
            target = f.root / "operator"
            target.write_text("keep")
            (f.output / "amd.json").symlink_to(target)
            status, result = f.cli("--vendor", "amd")
            self.assertEqual(status, 1)
            self.assertEqual(result["status"], "error")
            self.assertEqual(target.read_text(), "keep")


if __name__ == "__main__":
    unittest.main(verbosity=2)
