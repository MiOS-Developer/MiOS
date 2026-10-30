#!/usr/bin/env python3
# AI-hint: Static + series controls for hypr3d-mios-input-transitions.patch: leaving the Hypr3D room always hands the keyboard to a window (the room's exit focus, else the 2D owner saved at entry, else the newest still-shown window in the focus history, else the newest window on the room monitor; never under a session lock). The pre-fix patch (exit focus only) is the negative control.
# AI-related: usr/share/mios/hyprland/patches/hypr3d-mios-input-transitions.patch, usr/share/mios/hyprland/patches/hypr3d-mios-room-input.patch, usr/share/mios/mios.toml [desktop.hyprland.hypr3d].patches, tests/test-hypr3d-input-transitions-live.py
# AI-functions: Patch, Upstream
# Tier 1 needs only the tree. Tier 2 (MIOS_H3D_UPSTREAM = a checkout of the current upstream HEAD) applies the whole SSOT
# series and inspects the patched deactivate3D/enter3D; the live key-delivery proof is the opt-in live test.
import os
import re
import shutil
import subprocess
import tempfile
import tomllib
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATCH = "usr/share/mios/hyprland/patches/hypr3d-mios-input-transitions.patch"
ROOM_INPUT = "usr/share/mios/hyprland/patches/hypr3d-mios-room-input.patch"
UPSTREAM = os.environ.get("MIOS_H3D_UPSTREAM", "")
PRE_FIX = "2067aed55"  # the rejected exit-focus-only patch (kept in history as the negative control)


def ssot():
    with open(os.path.join(ROOT, "usr/share/mios/mios.toml"), "rb") as f:
        return tomllib.load(f)


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def pre_fix_patch():
    r = subprocess.run(["git", "-c", "safe.directory=*", "-C", ROOT, "show", f"{PRE_FIX}:{PATCH}"],
                       capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def added(patch):
    return "\n".join(l[1:] for l in patch.splitlines() if l.startswith("+") and not l.startswith("+++"))


def handback_defects(patch):
    """What a patch lacks for 'leaving the room always restores a focused window'. Empty = complete."""
    code = added(patch)
    missing = []
    if "g_preRoomFocus = Compat::focusedWindow()" not in code:
        missing.append("enter3D saves the 2D keyboard owner")
    if "!g_active || !g_preRoomFocus.lock()" not in code:
        missing.append("a re-entry during the exit keeps the original 2D owner")
    if "exitFocus = Compat::focusedWindow()" not in code:
        missing.append("deactivate3D captures the room focus before clearAimFocus")
    if "Desktop::History::windowTracker()" not in code:
        missing.append("focus-history fallback (the 2D owner closed in the room)")
    if "Desktop::windowState()->windows()" not in code:
        missing.append("last-resort window on the room monitor")
    if "isSessionLocked()" not in code:
        missing.append("session-lock guard")
    if "m_workspace->isVisible()" not in code:
        missing.append("visible-workspace guard")
    if "noFocus()" not in code:
        missing.append("no_focus guard")
    return missing


def function_body(src, name):
    m = re.search(rf"^static void {name}\(\) \{{\n(.*?)^\}}\n", src, re.S | re.M)
    return m.group(1) if m else ""


class Patch(unittest.TestCase):
    def test_ssot_trails_the_room_input_patch(self):
        patches = ssot()["desktop"]["hyprland"]["hypr3d"]["patches"]
        self.assertEqual(patches.count(PATCH), 1)
        self.assertEqual(patches[-1], PATCH, "the input-transitions patch is the trailing delta")
        self.assertEqual(patches[-2], ROOM_INPUT, "it is rebased onto the room-input patch")

    def test_header(self):
        head = read(PATCH).split("\ndiff --git", 1)[0]
        self.assertTrue(head.startswith("# AI-hint: "))
        self.assertIn("# AI-related: ", head)
        self.assertIn("no aim focus", head)

    def test_handback_is_complete(self):
        self.assertEqual(handback_defects(read(PATCH)), [])

    def test_pre_fix_patch_is_incomplete(self):
        """Negative control: the rejected patch only re-focused the room's exit focus."""
        old = pre_fix_patch()
        if old is None:
            self.skipTest(f"{PRE_FIX} not in this clone")
        missing = handback_defects(old)
        self.assertIn("enter3D saves the 2D keyboard owner", missing)
        self.assertIn("focus-history fallback (the 2D owner closed in the room)", missing)


@unittest.skipUnless(UPSTREAM, "MIOS_H3D_UPSTREAM (checkout of the current upstream HEAD) not set")
class Upstream(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="h3d-it-")
        cls.work = os.path.join(cls.tmp, "src")
        subprocess.run(["git", "-c", "safe.directory=*", "clone", "-q", UPSTREAM, cls.work], check=True)
        cls.applied = []
        for p in ssot()["desktop"]["hyprland"]["hypr3d"]["patches"]:
            if p == PATCH:
                cls.before = cls.snapshot()
            r = subprocess.run(["git", "apply", "--check", os.path.join(ROOT, p)], cwd=cls.work, capture_output=True,
                               text=True)
            cls.applied.append((p, r.returncode, r.stderr))
            if r.returncode == 0:
                subprocess.run(["git", "apply", os.path.join(ROOT, p)], cwd=cls.work, check=True)
        with open(os.path.join(cls.work, "src/main.cpp"), encoding="utf-8") as f:
            cls.src = f.read()

    @classmethod
    def snapshot(cls):
        d = os.path.join(cls.tmp, "before")
        shutil.copytree(cls.work, d, symlinks=True)
        return d

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_series_applies_in_order(self):
        for p, rc, err in self.applied:
            self.assertEqual(rc, 0, f"{p}: {err}")

    def test_pre_fix_patch_does_not_apply_on_the_room_input_series(self):
        """Negative control for the rebase: the old patch's deactivate3D context predates room-input."""
        old = pre_fix_patch()
        if old is None:
            self.skipTest(f"{PRE_FIX} not in this clone")
        path = os.path.join(self.tmp, "old.patch")
        with open(path, "w", encoding="utf-8") as f:
            f.write(old)
        r = subprocess.run(["git", "apply", "--check", path], cwd=self.before, capture_output=True)
        self.assertNotEqual(r.returncode, 0)

    def test_needs_the_room_input_patch(self):
        d = os.path.join(self.tmp, "no-room-input")
        subprocess.run(["git", "-c", "safe.directory=*", "clone", "-q", UPSTREAM, d], check=True)
        for p in ssot()["desktop"]["hyprland"]["hypr3d"]["patches"]:
            if p in (PATCH, ROOM_INPUT):
                continue
            subprocess.run(["git", "apply", os.path.join(ROOT, p)], cwd=d, check=True)
        r = subprocess.run(["git", "apply", "--check", os.path.join(ROOT, PATCH)], cwd=d, capture_output=True)
        self.assertNotEqual(r.returncode, 0)

    def test_deactivate_hands_the_keyboard_back_after_the_layout_restore(self):
        body = function_body(self.src, "deactivate3D")
        self.assertTrue(body)
        capture = body.index("exitFocus = Compat::focusedWindow()")
        self.assertLess(capture, body.index("clearAimFocus();"), "the room focus is read before clearAimFocus drops it")
        restore = body.index("restoreFocusAfterRoom(exitFocus)")
        self.assertGreater(restore, body.index("unghostWindows();"), "the window is back in its 2D place first")
        self.assertLess(restore, body.index("fireOnState(false)"), "focus is back before the state hooks run")

    def test_enter_saves_the_2d_owner_before_clearing_focus(self):
        body = function_body(self.src, "enter3D")
        save = body.index("g_preRoomFocus = Compat::focusedWindow()")
        self.assertLess(save, body.index("g_active = true;"), "read g_active before it is set (re-entry detection)")
        self.assertLess(save, body.index("clearAimFocus();"))

    def test_fallback_order(self):
        m = re.search(r"static PHLWINDOW roomExitFocusTarget\(.*?\n\}\n", self.src, re.S)
        self.assertIsNotNone(m)
        fn = m.group(0)
        order = [fn.index(s) for s in ("exitFocus.lock()", "g_preRoomFocus.lock()", "windowTracker()",
                                       "Desktop::windowState()->windows()")]
        self.assertEqual(order, sorted(order), "exit focus, then the 2D owner, then history, then any window")


if __name__ == "__main__":
    unittest.main()
