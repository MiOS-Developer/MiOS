#!/usr/bin/env python3 -I
# AI-hint: Executable tests for the native VM/remote host-key bindings lane (vm.rs + global_bindings.rs
# wiring): mios-inputd check/resolve-action/dispatch/normalize/map/vm-action against fixture SSOT roots,
# with the real binaries only. Absence of a binary carrying the vm-action surface FAILS (no skip-as-pass).
# AI-related: tools/native/mios-inputd/src/{vm.rs,config.rs,main.rs}, tools/native/mios-gen/src/global_bindings.rs,
# usr/share/mios/mios.toml [keybindings.global]
import json
import re
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _find_inputd():
    explicit = os.environ.get("MIOS_INPUTD")
    candidates = [Path(explicit)] if explicit else [
        Path("/var/lib/mios-build/vm-lane-target/x86_64-unknown-linux-musl/release/mios-inputd"),
        ROOT / "usr/libexec/mios/mios-inputd",
        Path("/usr/libexec/mios/mios-inputd"),
        Path("/usr/bin/mios-inputd"),
    ]
    for path in candidates:
        if path.is_file() and os.access(path, os.X_OK):
            probe = subprocess.run([str(path), "--help"], capture_output=True, text=True, timeout=10)
            if "vm-action" in probe.stdout + probe.stderr:
                return path
    raise AssertionError("mios-inputd with the vm-action surface is missing; build the VM lane first "
                         "or set MIOS_INPUTD to the fresh binary")


_INPUTD = _find_inputd()

VENDOR_TOML = "/usr/share/mios/mios.toml"
DRAWER_LINE_PREFIX = '"quickshell:drawer-toggle" = ['


def _vendor_keybindings_section():
    """All [keybindings*] tables from the live vendor SSOT verbatim (complete, valid),
    with two harmless test-only actions spliced into action_argv; the fixture stays
    under our control because run_inputd strips the MIOS_TOML* env that would
    shadow --root. The legacy [keybindings] tables and the [keybindings.global]
    family may sit far apart in the file; every keybindings-prefixed block is kept."""
    lines = Path(VENDOR_TOML).read_text().splitlines()
    def _is_table(line, prefix):
        t = line.lstrip()
        return t.startswith(prefix) or t.startswith("[" + prefix)
    out = []
    for prefix in ("[keybindings", "[desktop.hyprland.hypr3d"):
        for i, line in enumerate(lines):
            if _is_table(line, prefix):
                j = i + 1
                while j < len(lines) and not _is_table(lines[j], "["):
                    j += 1
                out.extend(lines[i:j])
                if prefix == "[keybindings" and any(_is_table(l, DRAWER_LINE_PREFIX) or l.lstrip().startswith(DRAWER_LINE_PREFIX) for l in lines[i:j]):
                    k = next(idx for idx, l in enumerate(out) if l.lstrip().startswith(DRAWER_LINE_PREFIX))
                    out[k + 1:k + 1] = ['"mios:test-true" = ["/bin/true"]', '"mios:test-false" = ["/bin/false"]']
    assert out, "no [keybindings*]/[desktop.hyprland.hypr3d*] tables found in the vendor SSOT"
    return "\n".join(out) + "\n"


FIXTURE_TOML = _vendor_keybindings_section()


def run_inputd(*args, root, timeout=20):
    # MIOS_TOML*/MIOS_TOML_ROOT in the caller's env shadow --root in mios-resolver;
    # the fixture root must win or the test would silently verify the live SSOT.
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("MIOS_TOML") and k not in ("MIOS_VENDOR_TOML", "MIOS_HOST_TOML")}
    return subprocess.run([str(_INPUTD), "--root", str(root), *args],
                          capture_output=True, text=True, timeout=timeout, env=env)


class TestVmBindings(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mios-vm-bindings-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        vendor = self.root / "usr/share/mios"
        vendor.mkdir(parents=True)
        (vendor / "mios.toml").write_text(FIXTURE_TOML)

    def test_check_valid_and_no_unresolved_actions(self):
        r = run_inputd("check", root=self.root)
        self.assertEqual(r.returncode, 0, r.stderr)
        doc = json.loads(r.stdout)
        self.assertTrue(doc["valid"])
        self.assertGreaterEqual(doc["bindings"], 1)
        self.assertEqual(doc["unresolved_actions"], [])
        # round-trip fidelity: check must echo the fixture's (vendor-copied) grab flag exactly
        vendor_text = Path(VENDOR_TOML).read_text()
        m = re.search(r"exclusive_grab\s*=\s*(true|false)", vendor_text)
        self.assertIsNotNone(m, "vendor controller table lost exclusive_grab")
        self.assertEqual(doc["exclusive_grab"], m.group(1) == "true")

    def test_resolve_action_two_sided(self):
        r = run_inputd("resolve-action", "mios:test-true", root=self.root)
        self.assertEqual(r.returncode, 0, r.stderr)
        doc = json.loads(r.stdout)
        self.assertEqual(doc["argv"], ["/bin/true"])
        self.assertFalse(doc["executed"], "resolve-action must never execute")
        n = run_inputd("resolve-action", "mios:not-configured", root=self.root)
        self.assertNotEqual(n.returncode, 0, "an unconfigured action must fail loudly")

    def test_dispatch_executes_configured_argv_two_sided(self):
        ok = run_inputd("dispatch", "mios:test-true", root=self.root)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        bad = run_inputd("dispatch", "mios:test-false", root=self.root)
        self.assertEqual(bad.returncode, 1, "the configured argv's own exit status must propagate")
        unknown = run_inputd("dispatch", "mios:not-configured", root=self.root)
        self.assertNotEqual(unknown.returncode, 0)

    def test_normalize_keeps_rightctrl_distinct(self):
        def norm(chord):
            r = subprocess.run([str(_INPUTD), "normalize", chord], capture_output=True, text=True, timeout=10)
            self.assertEqual(r.returncode, 0, r.stderr)
            return r.stdout.strip()
        self.assertEqual(norm("RIGHTCTRL+Space"), "RIGHTCTRL+space")
        self.assertNotEqual(norm("RIGHTCTRL+Space"), norm("CTRL+Space"), "RIGHTCTRL must not fold into CTRL")
        self.assertEqual(norm("super+ctrl+p"), "SUPER+CTRL+p")
        r = subprocess.run([str(_INPUTD), "normalize", "SUPER+"], capture_output=True, text=True, timeout=10)
        self.assertNotEqual(r.returncode, 0, "a chord without a key must be rejected")

    def test_planned_and_implemented_conflict_is_rejected(self):
        vendor = self.root / "usr/share/mios/mios.toml"
        self.assertIn("planned_actions = []", FIXTURE_TOML, "vendor section changed; update the splice")
        vendor.write_text(FIXTURE_TOML.replace("planned_actions = []",
                                              'planned_actions = ["mios:test-true"]'))
        r = run_inputd("check", root=self.root)
        self.assertNotEqual(r.returncode, 0, "an action cannot be both implemented and planned")
        self.assertIn("both implemented and planned", r.stderr + r.stdout)

    def test_vm_action_refuses_without_a_focused_vm(self):
        r = run_inputd("vm-action", "toggle_vm_passthrough", root=self.root, timeout=15)
        self.assertNotEqual(r.returncode, 0,
                            "without a focused, configured VM client the action must refuse, not synthesize")
        self.assertNotIn("MIOS_VM_SENT", r.stdout + r.stderr, "no escape may be claimed as sent")

    def test_map_exposes_vm_configuration(self):
        r = run_inputd("map", root=self.root)
        self.assertEqual(r.returncode, 0, r.stderr)
        vm = json.loads(r.stdout)["global"]["vm"]
        self.assertIn("looking-glass-client", vm["looking_glass_classes"])
        self.assertEqual(vm["x11_rdp_escape_key"], "Control_R")
        self.assertEqual(vm["looking_glass_escape_key"], "Scroll_Lock")


if __name__ == "__main__":
    unittest.main(verbosity=2)
