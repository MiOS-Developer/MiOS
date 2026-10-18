# AI-hint: Hermetic tests that mios-theme-render --web never destroys user Gecko CSS (MiOS-owned marker block, timestamped backups) and that AppMenu.qml renders app-supplied strings as plain text.
# AI-related: usr/libexec/mios/mios-theme-render (apply_web, _merge_managed_css, _write_managed_css), usr/share/mios/quickshell/AppMenu.qml
# AI-functions: TestManagedCss, TestAppMenuPlainText, main

import glob
import importlib.machinery
import importlib.util
import os
import re
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
RENDER = os.path.join(ROOT, "usr", "libexec", "mios", "mios-theme-render")
APPMENU = os.path.join(ROOT, "usr", "share", "mios", "quickshell", "AppMenu.qml")


def load_render():
    loader = importlib.machinery.SourceFileLoader("mios_theme_render", RENDER)
    spec = importlib.util.spec_from_loader("mios_theme_render", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


R = load_render()
MIOS = "/* AI-hint: MiOS browser chrome for Zen/Firefox profiles */\n:root { --x: red !important; }\n"
MIOS2 = "/* AI-hint: MiOS browser chrome for Zen/Firefox profiles */\n:root { --x: blue !important; }\n"
USER = "@namespace url(http://www.mozilla.org/keymaster/gatekeeper/there.is.only.xul);\n#nav-bar { display: none; }\n"


class TestManagedCss(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "chrome", "userChrome.css")
        os.makedirs(os.path.dirname(self.path))

    def tearDown(self):
        self.tmp.cleanup()

    def read(self, p=None):
        with open(p or self.path, encoding="utf-8") as fh:
            return fh.read()

    def write(self, text):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def backups(self):
        return sorted(glob.glob(self.path + ".mios-backup-*"))

    # positive controls -------------------------------------------------------------------
    def test_new_file_gets_only_the_marked_block(self):
        self.assertIsNone(R._write_managed_css(self.path, MIOS))
        text = self.read()
        self.assertTrue(text.startswith(R._CSS_BEGIN))
        self.assertIn("--x: red", text)
        self.assertTrue(text.rstrip().endswith(R._CSS_END))
        self.assertEqual(self.backups(), [])

    def test_marked_block_is_replaced_and_user_text_around_it_kept(self):
        self.write("/* mine above */\n" + R._css_block(MIOS) + "#tabbrowser { color: green; }\n")
        self.assertIsNone(R._write_managed_css(self.path, MIOS2))
        text = self.read()
        self.assertTrue(text.startswith("/* mine above */\n"))
        self.assertTrue(text.endswith("#tabbrowser { color: green; }\n"))
        self.assertIn("--x: blue", text)
        self.assertNotIn("--x: red", text)
        self.assertEqual(text.count(R._CSS_BEGIN), 1)
        self.assertEqual(self.backups(), [])

    def test_rerender_is_idempotent(self):
        self.write(USER)
        R._write_managed_css(self.path, MIOS)
        once = self.read()
        n = len(self.backups())
        R._write_managed_css(self.path, MIOS)
        self.assertEqual(self.read(), once)
        self.assertEqual(len(self.backups()), n)

    def test_untouched_legacy_render_is_migrated_without_backup(self):
        self.write(MIOS)
        self.assertIsNone(R._write_managed_css(self.path, MIOS))
        self.assertEqual(self.read(), R._css_block(MIOS))

    # negative controls (the defect inputs) -----------------------------------------------
    def test_unmarked_user_css_is_preserved_and_backed_up(self):
        self.write(USER)
        backup = R._write_managed_css(self.path, MIOS)
        text = self.read()
        self.assertTrue(text.startswith(USER), "user CSS (and its leading @namespace) must stay first")
        self.assertIn(R._css_block(MIOS), text)
        self.assertIsNotNone(backup)
        self.assertEqual(self.read(backup), USER)
        self.assertRegex(os.path.basename(backup), r"\.mios-backup-\d{8}T\d{6}")

    def test_edited_legacy_render_is_backed_up_before_replacement(self):
        edited = MIOS + "#user-addition { color: pink; }\n"
        self.write(edited)
        backup = R._write_managed_css(self.path, MIOS2)
        self.assertEqual(self.read(backup), edited)
        self.assertEqual(self.read(), R._css_block(MIOS2))

    def test_damaged_block_keeps_preceding_user_text_and_backs_up(self):
        damaged = "/* mine */\n" + R._CSS_BEGIN + "\n:root{}\n#user-after-lost-end{}\n"
        self.write(damaged)
        backup = R._write_managed_css(self.path, MIOS)
        self.assertEqual(self.read(backup), damaged)
        self.assertTrue(self.read().startswith("/* mine */\n" + R._CSS_BEGIN))

    def test_repeated_backups_never_overwrite_each_other(self):
        self.write(USER)
        first = R._write_managed_css(self.path, MIOS)
        self.write(USER + "/* v2 */\n")
        second = R._write_managed_css(self.path, MIOS)
        self.assertNotEqual(first, second)
        self.assertEqual(self.read(first), USER)

    def test_non_utf8_user_css_is_left_untouched(self):
        with open(self.path, "wb") as fh:
            fh.write(b"#a { content: '\xff\xfe'; }\n")
        self.assertIsNone(R._write_managed_css(self.path, MIOS))
        with open(self.path, "rb") as fh:
            self.assertEqual(fh.read(), b"#a { content: '\xff\xfe'; }\n")


def unsafe_texts(qml):
    """Line numbers of Text elements that do not force Text.PlainText."""
    bad = []
    for m in re.finditer(r"\bText\s*\{", qml):
        depth, i = 0, m.end() - 1
        while i < len(qml):
            depth += {"{": 1, "}": -1}.get(qml[i], 0)
            if depth == 0:
                break
            i += 1
        if "textFormat: Text.PlainText" not in qml[m.end():i]:
            bad.append(qml[:m.start()].count("\n") + 1)
    return bad


class TestAppMenuPlainText(unittest.TestCase):
    def test_every_text_in_appmenu_is_plain_text(self):
        with open(APPMENU, encoding="utf-8") as fh:
            qml = fh.read()
        self.assertGreaterEqual(qml.count("Text {"), 3)
        self.assertEqual(unsafe_texts(qml), [], "Text elements without PlainText (app strings render as markup)")

    def test_checker_flags_autotext_label(self):
        # Negative control: the pre-fix shape (AutoText default) must be caught.
        qml = "Item {\n  Text {\n    id: itemLabel\n    text: rowBox.it.label\n  }\n}\n"
        self.assertEqual(unsafe_texts(qml), [2])


if __name__ == "__main__":
    unittest.main(verbosity=2)
