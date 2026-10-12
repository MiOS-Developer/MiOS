#!/usr/bin/env python3
# AI-hint: Consolidated UX test suite: audio/focus cues, biometric lock, themes (btop/tmux/sync), clipboard, diff auditor, config gens, fastfetch, firstboot, fonts, GNOME ext, wallpapers, notifications, status bar.
"""Consolidated MiOS UX test suite (folded from 18 per-feature test files)."""

from __future__ import annotations

import sys
import unittest


# ======================================================================
# from tests/test-ux.py  (prefix af_)
# ======================================================================
"""Unit and integration test suite for AudioFeedbackEngine and audio_feedback CLI."""


import importlib.util
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

af__HERE = os.path.dirname(os.path.abspath(__file__))
af__ROOT = os.path.normpath(os.path.join(af__HERE, ".."))
af__TARGET_PATH = os.path.join(af__ROOT, "usr", "libexec", "mios", "ux", "audio_feedback.py")

af_spec = importlib.util.spec_from_file_location("audio_feedback", af__TARGET_PATH)
if af_spec and af_spec.loader:
    audio_feedback = importlib.util.module_from_spec(af_spec)
    sys.modules[af_spec.name] = audio_feedback
    af_spec.loader.exec_module(audio_feedback)
else:
    raise ImportError(f"Could not load module from {af__TARGET_PATH}")

class af_TestAudioFeedback(unittest.TestCase):
    """Test suite for event chord synthesis, PCM rendering, and audio feedback cues."""

    def test_event_chord_map_completeness(self):
        self.assertIn("completed", audio_feedback.EVENT_CHORD_MAP)
        self.assertIn("started", audio_feedback.EVENT_CHORD_MAP)
        self.assertIn("requires_input", audio_feedback.EVENT_CHORD_MAP)
        self.assertIn("warning", audio_feedback.EVENT_CHORD_MAP)
        self.assertIn("failed", audio_feedback.EVENT_CHORD_MAP)
        for name, data in audio_feedback.EVENT_CHORD_MAP.items():
            self.assertIn("freqs", data)
            self.assertIn("duration", data)
            self.assertIn("decay", data)

    def test_synthesize_event_pcm(self):
        with tempfile.TemporaryDirectory(prefix="mios-audio-test-") as tmpdir:
            wav_path = os.path.join(tmpdir, "completed.wav")
            samples = audio_feedback.synthesize_event_pcm("completed", wav_path, volume=0.5, sample_rate=22050)
            self.assertGreater(samples, 100)
            self.assertTrue(os.path.isfile(wav_path))
            self.assertGreater(os.path.getsize(wav_path), 200)

    def test_engine_synthesize_all_mock(self):
        engine = audio_feedback.AudioFeedbackEngine(volume_pct=60, mock=True)
        res = engine.synthesize_all("/tmp/sounds")
        self.assertEqual(len(res), len(audio_feedback.EVENT_CHORD_MAP))
        self.assertIn("completed", res)

    def test_play_cue_mock(self):
        engine = audio_feedback.AudioFeedbackEngine(volume_pct=75, mock=True)
        res = engine.play_cue("started")
        self.assertTrue(res["played"])
        self.assertEqual(res["event"], "started")
        self.assertEqual(res["backend"], "mock_pcm")
        self.assertEqual(res["volume_pct"], 75)

    def test_play_cue_unknown_event_raises(self):
        engine = audio_feedback.AudioFeedbackEngine(mock=True)
        with self.assertRaises(ValueError):
            engine.play_cue("unknown_event_xyz")

    def test_cli_play_event_mock(self):
        test_args = ["audio_feedback.py", "--event", "completed", "--volume", "50", "--mock", "--json"]
        with patch.object(sys, "argv", test_args):
            exit_code = audio_feedback.main()
            self.assertEqual(exit_code, 0)

    def test_cli_synthesize_to_mock(self):
        test_args = ["audio_feedback.py", "--synthesize-to", "/tmp/mios-cues", "--mock", "--json"]
        with patch.object(sys, "argv", test_args):
            exit_code = audio_feedback.main()
            self.assertEqual(exit_code, 0)

def af_main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(af_TestAudioFeedback)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# ======================================================================
# from tests/test-ux.py  (prefix bl_)
# ======================================================================
"""Unit and integration test suite for BiometricLockManager and biometric_lock CLI."""


import importlib.util
import json
import os
import sys
import unittest
from unittest.mock import patch

bl__HERE = os.path.dirname(os.path.abspath(__file__))
bl__ROOT = os.path.normpath(os.path.join(bl__HERE, ".."))
bl__TARGET_PATH = os.path.join(bl__ROOT, "usr", "libexec", "mios", "ux", "biometric_lock.py")

bl_spec = importlib.util.spec_from_file_location("biometric_lock", bl__TARGET_PATH)
if bl_spec and bl_spec.loader:
    biometric_lock = importlib.util.module_from_spec(bl_spec)
    sys.modules[bl_spec.name] = biometric_lock
    bl_spec.loader.exec_module(biometric_lock)
else:
    raise ImportError(f"Could not load module from {bl__TARGET_PATH}")

class bl_TestBiometricLock(unittest.TestCase):
    """Test suite for biometric hardware inspection, PAM stack generation, and lock triggers."""

    def test_biometric_sensor_dataclass(self):
        s = biometric_lock.BiometricSensor(
            sensor_type="fingerprint",
            device_name="Synaptics Prometheus",
            driver="pam_fprintd",
            is_enrolled=True,
            status="ready",
            capabilities=["touch", "verification"],
        )
        self.assertEqual(s.sensor_type, "fingerprint")
        self.assertTrue(s.is_enrolled)
        self.assertEqual(len(s.capabilities), 2)

    def test_check_sensors_mock(self):
        manager = biometric_lock.BiometricLockManager(mock=True)
        res = manager.check_sensors()
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["sensors_detected"], 2)
        sensor_types = [s["sensor_type"] for s in res["sensors"]]
        self.assertIn("fingerprint", sensor_types)
        self.assertIn("fido2_ctap2", sensor_types)

    def test_render_pam_config_with_password_fallback(self):
        manager = biometric_lock.BiometricLockManager(mock=True)
        pam = manager.render_pam_config("swaylock")
        self.assertIn("pam_fprintd.so", pam)
        self.assertIn("pam_u2f.so", pam)
        # Unconditional password fallback guarantee
        self.assertIn("auth        include       system-auth", pam)
        self.assertIn("account     include       system-auth", pam)

    def test_generate_pam_files_mock(self):
        manager = biometric_lock.BiometricLockManager(mock=True)
        res = manager.generate_pam_files(services=["swaylock", "hyprlock"])
        self.assertEqual(res["status"], "success")
        self.assertEqual(len(res["files"]), 2)
        self.assertIn("swaylock", res["previews"])
        self.assertIn("hyprlock", res["previews"])

    def test_lock_screen_mock(self):
        manager = biometric_lock.BiometricLockManager(mock=True)
        res = manager.lock_screen()
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["action"], "lock_screen")
        self.assertIn("swaylock", res["command"])

    def test_cli_check_sensors_mock(self):
        test_args = ["biometric_lock.py", "--check-sensors", "--mock", "--json"]
        with patch.object(sys, "argv", test_args):
            exit_code = biometric_lock.main()
            self.assertEqual(exit_code, 0)

    def test_cli_generate_pam_mock(self):
        test_args = ["biometric_lock.py", "--generate-pam", "--target-service", "swaylock", "--mock", "--json"]
        with patch.object(sys, "argv", test_args):
            exit_code = biometric_lock.main()
            self.assertEqual(exit_code, 0)

    def test_cli_lock_mock(self):
        test_args = ["biometric_lock.py", "--lock", "--mock", "--json"]
        with patch.object(sys, "argv", test_args):
            exit_code = biometric_lock.main()
            self.assertEqual(exit_code, 0)

def bl_main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(bl_TestBiometricLock)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# ======================================================================
# from tests/test-ux.py  (prefix bt_)
# ======================================================================
"""Unit and integration test suite for BtopThemeRenderer and btop_theme CLI."""


import importlib.util
import json
import os
import sys
import unittest
from unittest.mock import patch

bt__HERE = os.path.dirname(os.path.abspath(__file__))
bt__ROOT = os.path.normpath(os.path.join(bt__HERE, ".."))
bt__TARGET_PATH = os.path.join(bt__ROOT, "usr", "libexec", "mios", "ux", "btop_theme.py")

if os.path.isfile(bt__TARGET_PATH):
    bt_spec = importlib.util.spec_from_file_location("btop_theme", bt__TARGET_PATH)
    if bt_spec and bt_spec.loader:
        btop_theme = importlib.util.module_from_spec(bt_spec)
        sys.modules[bt_spec.name] = btop_theme
        bt_spec.loader.exec_module(btop_theme)
    else:
        btop_theme = None
else:
    btop_theme = None

@unittest.skipIf(btop_theme is None, "btop_theme.py was strangler-deleted (ported to native mios-gen render-btop-theme)")
class bt_TestBtopTheme(unittest.TestCase):
    """Test suite for btop theme rendering and exact RGB hex palette mapping."""

    def test_renderer_init_and_palette(self):
        renderer = btop_theme.BtopThemeRenderer(mock=True)
        palette = renderer.get_palette()
        self.assertIn("bg", palette)
        self.assertIn("accent", palette)
        self.assertIn("cursor", palette)

    def test_build_theme_mapping(self):
        renderer = btop_theme.BtopThemeRenderer(mock=True)
        mapping = renderer.build_theme_mapping()
        self.assertIn("main_bg", mapping)
        self.assertIn("main_fg", mapping)
        self.assertIn("cpu_box", mapping)
        self.assertIn("mem_box", mapping)
        self.assertIn("temp_start", mapping)
        self.assertIn("temp_end", mapping)
        self.assertIn("upload_start", mapping)
        self.assertIn("download_start", mapping)

    def test_render_theme_text_and_validate(self):
        renderer = btop_theme.BtopThemeRenderer(mock=True)
        text = renderer.render_theme_text()
        self.assertIn('# MiOS Btop System Monitor Theme', text)
        self.assertIn('theme[main_bg]=', text)
        self.assertIn('theme[main_fg]=', text)
        valid, errors = renderer.validate_theme_content(text)
        self.assertTrue(valid, f"Theme validation failed: {errors}")
        self.assertEqual(len(errors), 0)

    def test_validate_theme_content_invalid(self):
        renderer = btop_theme.BtopThemeRenderer(mock=True)
        invalid_text = 'theme[main_bg]="NOT_A_HEX"'
        valid, errors = renderer.validate_theme_content(invalid_text)
        self.assertFalse(valid)
        self.assertGreater(len(errors), 0)

    def test_render_mock(self):
        renderer = btop_theme.BtopThemeRenderer(mock=True)
        res = renderer.render()
        self.assertEqual(res["status"], "success")
        self.assertGreater(res["theme_len"], 100)
        self.assertTrue(res["mock"])

    def test_check_mock(self):
        renderer = btop_theme.BtopThemeRenderer(mock=True)
        check_res = renderer.check()
        self.assertEqual(check_res["status"], "valid")
        self.assertTrue(check_res["mock"])

    def test_cli_render_mock(self):
        test_args = ["btop_theme.py", "--render", "--mock", "--json"]
        with patch.object(sys, "argv", test_args):
            exit_code = btop_theme.main()
            self.assertEqual(exit_code, 0)

    def test_cli_check_mock(self):
        test_args = ["btop_theme.py", "--check", "mios.theme", "--mock", "--json"]
        with patch.object(sys, "argv", test_args):
            exit_code = btop_theme.main()
            self.assertEqual(exit_code, 0)

    def test_cli_user_flag_mock(self):
        test_args = ["btop_theme.py", "--render", "--user", "--mock", "--json"]
        with patch.object(sys, "argv", test_args):
            exit_code = btop_theme.main()
            self.assertEqual(exit_code, 0)

def bt_main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(bt_TestBtopTheme)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# ======================================================================
# from tests/test-ux.py  (prefix cs_)
# ======================================================================
"""Unit and integration test suite for ClipboardSyncEngine and clipboard_sync CLI."""


import importlib.util
import json
import os
import sys
import unittest
from unittest.mock import patch

cs__HERE = os.path.dirname(os.path.abspath(__file__))
cs__ROOT = os.path.normpath(os.path.join(cs__HERE, ".."))
cs__TARGET_PATH = os.path.join(cs__ROOT, "usr", "libexec", "mios", "ux", "clipboard_sync.py")

cs_spec = importlib.util.spec_from_file_location("clipboard_sync", cs__TARGET_PATH)
if cs_spec and cs_spec.loader:
    clipboard_sync = importlib.util.module_from_spec(cs_spec)
    sys.modules[cs_spec.name] = clipboard_sync
    cs_spec.loader.exec_module(clipboard_sync)
else:
    raise ImportError(f"Could not load module from {cs__TARGET_PATH}")

class cs_TestClipboardSync(unittest.TestCase):
    """Test suite for sensitive token redaction and host-to-guest clipboard synchronization."""

    def test_redact_openai_key(self):
        engine = clipboard_sync.ClipboardSyncEngine(mock=True)
        raw = "My key is sk-1234567890abcdef1234567890 for API calls"
        res = engine.filter_text(raw)
        self.assertNotIn("sk-1234567890abcdef1234567890", res.redacted_text)
        self.assertIn("[REDACTED_SECRET:OPENAI_KEY]", res.redacted_text)
        self.assertIn("OPENAI_KEY", res.detected_categories)
        self.assertEqual(res.redactions_count, 1)

    def test_redact_github_pat(self):
        engine = clipboard_sync.ClipboardSyncEngine(mock=True)
        raw = "git clone https://ghp_1234567890abcdefghijklmnopqrstuv@github.com/repo.git"
        res = engine.filter_text(raw)
        self.assertNotIn("ghp_1234567890abcdefghijklmnopqrstuv", res.redacted_text)
        self.assertIn("[REDACTED_SECRET:GITHUB_PAT]", res.redacted_text)
        self.assertIn("GITHUB_PAT", res.detected_categories)

    def test_redact_aws_keys(self):
        engine = clipboard_sync.ClipboardSyncEngine(mock=True)
        raw = "export AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE\nexport AWS_SECRET_ACCESS_KEY='wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY'"
        res = engine.filter_text(raw)
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", res.redacted_text)
        self.assertNotIn("wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", res.redacted_text)
        self.assertIn("AWS_ACCESS_KEY", res.detected_categories)
        self.assertIn("AWS_SECRET_KEY", res.detected_categories)

    def test_redact_private_key_block(self):
        engine = clipboard_sync.ClipboardSyncEngine(mock=True)
        raw = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA0Y1...\n-----END RSA PRIVATE KEY-----"
        res = engine.filter_text(raw)
        self.assertNotIn("MIIEowIBAAKCAQEA0Y1", res.redacted_text)
        self.assertIn("[REDACTED_SECRET:PRIVATE_KEY]", res.redacted_text)
        self.assertIn("PRIVATE_KEY", res.detected_categories)

    def test_redact_bearer_and_slack_tokens(self):
        engine = clipboard_sync.ClipboardSyncEngine(mock=True)
        raw = "Authorization: Bearer my-super-secret-bearer-token-1234567890\nSlack: xoxb-1234567890-abcdefghij"
        res = engine.filter_text(raw)
        self.assertNotIn("my-super-secret-bearer-token-1234567890", res.redacted_text)
        self.assertNotIn("xoxb-1234567890-abcdefghij", res.redacted_text)
        self.assertIn("BEARER_TOKEN", res.detected_categories)
        self.assertIn("SLACK_TOKEN", res.detected_categories)

    def test_sync_once_mock(self):
        engine = clipboard_sync.ClipboardSyncEngine(mock=True)
        engine.set_mock_clipboard("Normal text with sk-99887766554433221100aabb secret")
        res = engine.sync_once()
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["redactions_count"], 1)
        self.assertIn("OPENAI_KEY", res["detected_categories"])

    def test_get_stats_report(self):
        engine = clipboard_sync.ClipboardSyncEngine(mock=True)
        engine.filter_text("sk-abcdef1234567890abcdef1234")
        report = engine.get_stats_report()
        self.assertEqual(report["status"], "success")
        self.assertGreaterEqual(report["total_redactions"], 1)
        self.assertIn("OPENAI_KEY", report["categories"])

    def test_cli_filter_text_mock(self):
        test_args = ["clipboard_sync.py", "--filter-text", "sk-1234567890abcdef1234567890", "--mock", "--json"]
        with patch.object(sys, "argv", test_args):
            exit_code = clipboard_sync.main()
            self.assertEqual(exit_code, 0)

    def test_cli_sync_mock(self):
        test_args = ["clipboard_sync.py", "--sync", "--mock", "--json"]
        with patch.object(sys, "argv", test_args):
            exit_code = clipboard_sync.main()
            self.assertEqual(exit_code, 0)

    def test_cli_stats_mock(self):
        test_args = ["clipboard_sync.py", "--stats", "--mock", "--json"]
        with patch.object(sys, "argv", test_args):
            exit_code = clipboard_sync.main()
            self.assertEqual(exit_code, 0)

def cs_main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(cs_TestClipboardSync)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# ======================================================================
# from tests/test-ux.py  (prefix da_)
# ======================================================================
"""Unit and integration test suite for DiffAuditorEngine and diff_auditor CLI."""


import importlib.util
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

da__HERE = os.path.dirname(os.path.abspath(__file__))
da__ROOT = os.path.normpath(os.path.join(da__HERE, ".."))
da__TARGET_PATH = os.path.join(da__ROOT, "usr", "libexec", "mios", "ux", "diff_auditor.py")

da_spec = importlib.util.spec_from_file_location("diff_auditor", da__TARGET_PATH)
if da_spec and da_spec.loader:
    diff_auditor = importlib.util.module_from_spec(da_spec)
    sys.modules[da_spec.name] = diff_auditor
    da_spec.loader.exec_module(diff_auditor)
else:
    raise ImportError(f"Could not load module from {da__TARGET_PATH}")

class da_TestDiffAuditor(unittest.TestCase):
    """Test suite for accrued diff auditing, operator approval/rejection, and manifest staging."""

    def test_load_ledger_mock(self):
        engine = diff_auditor.DiffAuditorEngine(mock=True)
        ledger = engine.load_ledger()
        self.assertEqual(ledger["total_accrued"], 3)
        self.assertEqual(ledger["safe_count"], 1)
        self.assertEqual(ledger["high_risk_count"], 2)

    def test_list_entries_mock(self):
        engine = diff_auditor.DiffAuditorEngine(mock=True)
        entries = engine.list_entries()
        self.assertEqual(len(entries), 3)
        paths = [e["path"] for e in entries]
        self.assertIn("var/lib/mios/ai/skills/custom-agent.md", paths)
        self.assertIn("etc/pam.d/system-auth", paths)

    def test_process_decisions_approve_safe(self):
        with tempfile.TemporaryDirectory(prefix="mios-audit-test-") as tmpdir:
            staged_file = os.path.join(tmpdir, "staged.json")
            engine = diff_auditor.DiffAuditorEngine(staged_out=staged_file, mock=True)
            manifest = engine.process_decisions(approve_safe=True)
            self.assertEqual(manifest["total_approved"], 1)
            self.assertEqual(manifest["approved_diffs"][0]["path"], "var/lib/mios/ai/skills/custom-agent.md")
            self.assertTrue(manifest["bake_ready"])
            self.assertTrue(os.path.isfile(staged_file))

    def test_process_decisions_approve_paths_and_reject(self):
        with tempfile.TemporaryDirectory(prefix="mios-audit-test-") as tmpdir:
            staged_file = os.path.join(tmpdir, "staged.json")
            engine = diff_auditor.DiffAuditorEngine(staged_out=staged_file, mock=True)
            manifest = engine.process_decisions(
                approve_paths=["etc/mios/profile.toml"],
                reject_paths=["etc/pam.d/system-auth"],
            )
            self.assertEqual(manifest["total_approved"], 1)
            self.assertEqual(manifest["total_rejected"], 1)
            self.assertEqual(manifest["total_pending"], 1)
            self.assertEqual(manifest["approved_diffs"][0]["path"], "etc/mios/profile.toml")
            self.assertEqual(manifest["rejected_diffs"][0]["path"], "etc/pam.d/system-auth")

    def test_process_decisions_approve_all(self):
        with tempfile.TemporaryDirectory(prefix="mios-audit-test-") as tmpdir:
            staged_file = os.path.join(tmpdir, "staged.json")
            engine = diff_auditor.DiffAuditorEngine(staged_out=staged_file, mock=True)
            manifest = engine.process_decisions(approve_all=True)
            self.assertEqual(manifest["total_approved"], 3)
            self.assertEqual(manifest["total_rejected"], 0)
            self.assertEqual(manifest["total_pending"], 0)

    def test_cli_list_mock(self):
        test_args = ["diff_auditor.py", "--list", "--mock", "--json"]
        with patch.object(sys, "argv", test_args):
            exit_code = diff_auditor.main()
            self.assertEqual(exit_code, 0)

    def test_cli_approve_safe_mock(self):
        with tempfile.TemporaryDirectory(prefix="mios-audit-cli-") as tmpdir:
            staged_file = os.path.join(tmpdir, "staged.json")
            test_args = ["diff_auditor.py", "--approve-safe", "--staged-out", staged_file, "--mock", "--json"]
            with patch.object(sys, "argv", test_args):
                exit_code = diff_auditor.main()
                self.assertEqual(exit_code, 0)

def da_main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(da_TestDiffAuditor)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# ======================================================================
# from tests/test-ux.py  (prefix ecg_)
# ======================================================================
"""Unit and integration test suite for EditorConfigGen and editor_config_gen CLI."""


import importlib.util
import json
import os
import sys
import unittest
from unittest.mock import patch

ecg__HERE = os.path.dirname(os.path.abspath(__file__))
ecg__ROOT = os.path.normpath(os.path.join(ecg__HERE, ".."))
ecg__TARGET_PATH = os.path.join(ecg__ROOT, "usr", "libexec", "mios", "ux", "editor_config_gen.py")

ecg_spec = importlib.util.spec_from_file_location("editor_config_gen", ecg__TARGET_PATH)
if ecg_spec and ecg_spec.loader:
    editor_config_gen = importlib.util.module_from_spec(ecg_spec)
    sys.modules[ecg_spec.name] = editor_config_gen
    ecg_spec.loader.exec_module(editor_config_gen)
else:
    raise ImportError(f"Could not load module from {ecg__TARGET_PATH}")

class ecg_TestEditorConfigGen(unittest.TestCase):
    """Test suite for developer editor (VS Code, Cursor, Continue) local AI redirection generation."""

    def test_editor_config_gen_init(self):
        gen = editor_config_gen.EditorConfigGen(
            inference_endpoint="http://localhost:11450/v1",
            embed_model="nomic-embed-text",
            mock=True,
        )
        self.assertTrue(gen.agent_endpoint.startswith("http://localhost:"))
        self.assertEqual(gen.inference_endpoint, "http://localhost:11450/v1")
        self.assertTrue(len(gen.default_model) > 0)
        self.assertEqual(gen.embed_model, "nomic-embed-text")

    def test_render_vscode_settings(self):
        gen = editor_config_gen.EditorConfigGen(mock=True)
        vscode = gen.render_vscode_settings()
        self.assertIn("github.copilot.advanced", vscode)
        self.assertIn("openai.apiBase", vscode)
        self.assertIn("openai.model", vscode)
        self.assertEqual(vscode["openai.apiBase"], gen.agent_endpoint)
        self.assertEqual(vscode["openai.model"], gen.default_model)

    def test_render_cursor_settings(self):
        gen = editor_config_gen.EditorConfigGen(mock=True)
        cursor = gen.render_cursor_settings()
        self.assertIn("cursor.general.openAiBaseUrl", cursor)
        self.assertIn("cursor.general.customModels", cursor)
        custom_models = [m["name"] for m in cursor["cursor.general.customModels"]]
        self.assertIn(gen.default_model, custom_models)
        self.assertIn("mios-llm-light", custom_models)

    def test_render_continue_config(self):
        gen = editor_config_gen.EditorConfigGen(mock=True)
        cont = gen.render_continue_config()
        self.assertIn("models", cont)
        self.assertIn("tabAutocompleteModel", cont)
        self.assertIn("embeddingsProvider", cont)
        self.assertEqual(cont["embeddingsProvider"]["model"], "nomic-embed-text")

    def test_generate_all_targets(self):
        gen = editor_config_gen.EditorConfigGen(mock=True)
        res = gen.generate(target="all")
        self.assertEqual(res["status"], "success")
        self.assertIn("vscode", res["configurations"])
        self.assertIn("cursor", res["configurations"])
        self.assertIn("continue", res["configurations"])
        self.assertEqual(len(res["files"]), 3)

    def test_check_mock(self):
        gen = editor_config_gen.EditorConfigGen(mock=True)
        check_res = gen.check("mock-settings.json")
        self.assertEqual(check_res["status"], "compliant")
        self.assertTrue(check_res["local_endpoint"])
        self.assertFalse(check_res["cloud_keys_detected"])

    def check_real_config(self, content):
        with patch.object(editor_config_gen, "mios_toml", None):
            gen = editor_config_gen.EditorConfigGen()
        with tempfile.TemporaryDirectory(prefix="mios-editor-check-") as tmp:
            path = os.path.join(tmp, "settings.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(content, fh)
            return gen.check(path)

    def test_check_accepts_all_generated_routes(self):
        with patch.object(editor_config_gen, "mios_toml", None):
            gen = editor_config_gen.EditorConfigGen()
        for config in (gen.render_vscode_settings(), gen.render_cursor_settings(),
                       gen.render_continue_config()):
            with self.subTest(config=config):
                self.assertEqual(self.check_real_config(config)["status"], "compliant")

    def test_check_rejects_each_unapproved_endpoint(self):
        for endpoint in ("https://unapproved.invalid/v1", "http://localhost.evil.invalid/v1",
                         "http://localhost:9999/v1", "http://localhost:8700/v1?forward=external",
                         "http://user:pass@localhost:8700/v1", "not-a-url", 8700):
            with self.subTest(endpoint=endpoint):
                result = self.check_real_config({
                    "openai.apiBase": "http://localhost:8700/v1",
                    "models": [{"apiBase": endpoint}],
                })
                self.assertEqual(result["status"], "non-compliant")
                self.assertFalse(result["local_endpoint"])
                self.assertEqual(result["invalid_endpoint_fields"], ["models[0].apiBase"])

    def test_check_localhost_in_prose_is_not_an_endpoint(self):
        result = self.check_real_config({"description": "connect to localhost"})
        self.assertEqual(result["status"], "non-compliant")
        self.assertFalse(result["local_endpoint"])

    def test_check_local_placeholder_key_is_allowed(self):
        result = self.check_real_config({
            "openai.apiBase": "http://localhost:8700/v1",
            "openai.apiKey": "sk-local-fixture",
        })
        self.assertEqual(result["status"], "compliant")
        self.assertFalse(result["cloud_keys_detected"])

    def test_check_rejects_credential_without_echoing_it(self):
        key = "sk-" + "synthetic-cloud-fixture"
        result = self.check_real_config({
            "openai.apiBase": "http://localhost:8700/v1", "openai.apiKey": key,
        })
        self.assertEqual(result["status"], "non-compliant")
        self.assertTrue(result["cloud_keys_detected"])
        self.assertNotIn(key, json.dumps(result))

    def test_check_ignores_unrelated_schema_and_prose(self):
        result = self.check_real_config({
            "openai.apiBase": "http://localhost:8700/v1",
            "$schema": "https://schemas.invalid/editor.json",
            "description": "Use a sk-local placeholder for localhost",
        })
        self.assertEqual(result["status"], "compliant")

    def test_cli_generate_all_mock(self):
        test_args = ["editor_config_gen.py", "--generate", "--target", "all", "--mock", "--json"]
        with patch.object(sys, "argv", test_args):
            exit_code = editor_config_gen.main()
            self.assertEqual(exit_code, 0)

    def test_cli_target_vscode_mock(self):
        test_args = ["editor_config_gen.py", "--generate", "--target", "vscode", "--mock", "--json"]
        with patch.object(sys, "argv", test_args):
            exit_code = editor_config_gen.main()
            self.assertEqual(exit_code, 0)

    def test_cli_check_mock(self):
        test_args = ["editor_config_gen.py", "--check", "settings.json", "--mock", "--json"]
        with patch.object(sys, "argv", test_args):
            exit_code = editor_config_gen.main()
            self.assertEqual(exit_code, 0)

def ecg_main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(ecg_TestEditorConfigGen)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# ======================================================================
# from tests/test-ux.py  (prefix fg_)
# ======================================================================
"""Unit and integration test suite for FastfetchGenEngine and fastfetch_gen CLI."""


import importlib.util
import json
import os
import sys
import unittest
from unittest.mock import patch

fg__HERE = os.path.dirname(os.path.abspath(__file__))
fg__ROOT = os.path.normpath(os.path.join(fg__HERE, ".."))
fg__TARGET_PATH = os.path.join(fg__ROOT, "usr", "libexec", "mios", "ux", "fastfetch_gen.py")

if os.path.isfile(fg__TARGET_PATH):
    fg_spec = importlib.util.spec_from_file_location("fastfetch_gen", fg__TARGET_PATH)
    if fg_spec and fg_spec.loader:
        fastfetch_gen = importlib.util.module_from_spec(fg_spec)
        sys.modules[fg_spec.name] = fastfetch_gen
        fg_spec.loader.exec_module(fastfetch_gen)
    else:
        fastfetch_gen = None
else:
    fastfetch_gen = None

@unittest.skipIf(fastfetch_gen is None, "fastfetch_gen.py was strangler-deleted (ported to native mios-gen render-fastfetch)")
class fg_TestFastfetchGen(unittest.TestCase):
    """Test suite for Fastfetch JSONC configuration and hardware/AI module generation."""

    def test_engine_init_and_palette(self):
        engine = fastfetch_gen.FastfetchGenEngine(logo_type="auto", mock=True)
        self.assertEqual(engine.logo_type, "auto")
        self.assertIn("bg", engine.palette)
        self.assertIn("accent", engine.palette)

    def test_inspect_system_metadata_mock(self):
        engine = fastfetch_gen.FastfetchGenEngine(mock=True)
        meta = engine.inspect_system_metadata()
        self.assertEqual(meta["os_name"], "MiOS Linux (bootc/OCI workstation)")
        self.assertIn("llama-swap", meta["ai_engine"])
        self.assertEqual(meta["active_model"], "Qwen2.5-Coder-7B-Instruct-GGUF")
        self.assertIn("ghcr.io/mios-dev/mios", meta["bootc_image"])

    def test_generate_jsonc_validity(self):
        engine = fastfetch_gen.FastfetchGenEngine(mock=True)
        jsonc_str = engine.generate_jsonc()
        parsed = json.loads(jsonc_str)
        self.assertIn("$schema", parsed)
        self.assertIn("modules", parsed)
        module_types = [m.get("type") for m in parsed["modules"]]
        self.assertIn("os", module_types)
        self.assertIn("cpu", module_types)
        self.assertIn("gpu", module_types)
        self.assertIn("memory", module_types)
        self.assertIn("custom", module_types)

    def test_run_pipeline(self):
        engine = fastfetch_gen.FastfetchGenEngine(mock=True)
        res = engine.run()
        self.assertEqual(res["status"], "success")
        self.assertGreater(res["jsonc_lines"], 10)
        self.assertTrue(res["mock"])

    def test_cli_generate_mock(self):
        test_args = ["fastfetch_gen.py", "--generate", "--mock", "--json"]
        with patch.object(sys, "argv", test_args):
            exit_code = fastfetch_gen.main()
            self.assertEqual(exit_code, 0)

    def test_cli_logo_type_mock(self):
        test_args = ["fastfetch_gen.py", "--logo-type", "none", "--mock", "--json"]
        with patch.object(sys, "argv", test_args):
            exit_code = fastfetch_gen.main()
            self.assertEqual(exit_code, 0)

def fg_main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(fg_TestFastfetchGen)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# ======================================================================
# from tests/test-ux.py  (prefix fw_)
# ======================================================================
"""Unit and integration test suite for FirstBootWizardEngine and CLI."""


import importlib.util
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

fw__HERE = os.path.dirname(os.path.abspath(__file__))
fw__ROOT = os.path.normpath(os.path.join(fw__HERE, ".."))
fw__TARGET_PATH = os.path.join(fw__ROOT, "usr", "libexec", "mios", "ux", "firstboot_wizard.py")

fw_spec = importlib.util.spec_from_file_location("firstboot_wizard", fw__TARGET_PATH)
if fw_spec and fw_spec.loader:
    firstboot_wizard = importlib.util.module_from_spec(fw_spec)
    sys.modules[fw_spec.name] = firstboot_wizard
    fw_spec.loader.exec_module(firstboot_wizard)
else:
    raise ImportError(f"Could not load module from {fw__TARGET_PATH}")

class fw_TestFirstbootWizard(unittest.TestCase):
    """Test suite for firstboot state machine, credential hashing, profile.toml materialization, and CLI."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="mios-test-wizard-")
        self.config_out = os.path.join(self.temp_dir.name, "profile.toml")
        self.sentinel_path = os.path.join(self.temp_dir.name, ".firstboot_done")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_state_machine_transitions(self):
        engine = firstboot_wizard.FirstBootWizardEngine(
            config_out=self.config_out,
            sentinel_path=self.sentinel_path,
            mock=False,
        )
        self.assertEqual(engine.state, firstboot_wizard.WizardState.INIT)

        # Step 1: Welcome
        engine.step_welcome()
        self.assertEqual(engine.state, firstboot_wizard.WizardState.WELCOME)

        # Step 2: Auth
        engine.step_identity_auth(username="mios", password="SecretPassword123")
        self.assertEqual(engine.state, firstboot_wizard.WizardState.IDENTITY_AUTH)
        self.assertTrue(engine.config.password_hash.startswith("$6$"))

        # Step 3: Network
        engine.step_network(ssid="MiOS-Lab", psk="LabPassphrase", sec="wpa-psk")
        self.assertEqual(engine.state, firstboot_wizard.WizardState.NETWORK)

        # Step 4: AI Brain
        engine.step_ai_brain(lane="mios-llm-light", model="Qwen2.5-Coder-7B-Instruct-GGUF", vram_mb=8192)
        self.assertEqual(engine.state, firstboot_wizard.WizardState.AI_BRAIN)

        # Step 5: Finalize
        res = engine.step_finalize()
        self.assertEqual(engine.state, firstboot_wizard.WizardState.COMPLETED)
        self.assertEqual(res["status"], "success")
        self.assertTrue(os.path.exists(self.config_out))
        self.assertTrue(os.path.exists(self.sentinel_path))

    def test_run_mock_preseed(self):
        engine = firstboot_wizard.FirstBootWizardEngine(
            config_out=self.config_out,
            sentinel_path=self.sentinel_path,
            mock=True,
        )
        res = engine.run()
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["config"]["username"], "mios")
        self.assertEqual(res["config"]["ai_lane"], "mios-llm-light")
        self.assertGreaterEqual(len(res["transitions"]), 4)

    def test_cli_execution_mock_json(self):
        test_args = [
            "firstboot_wizard.py",
            "--config-out", self.config_out,
            "--sentinel-path", self.sentinel_path,
            "--mock",
            "--json",
        ]
        with patch.object(sys, "argv", test_args):
            exit_code = firstboot_wizard.main()
            self.assertEqual(exit_code, 0)

def fw_main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(fw_TestFirstbootWizard)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# ======================================================================
# from tests/test-ux.py  (prefix fa_)
# ======================================================================
"""Unit and integration test suite for FocusAudioSynthesizer and focus_audio CLI."""


import importlib.util
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

fa__HERE = os.path.dirname(os.path.abspath(__file__))
fa__ROOT = os.path.normpath(os.path.join(fa__HERE, ".."))
fa__TARGET_PATH = os.path.join(fa__ROOT, "usr", "libexec", "mios", "ux", "focus_audio.py")

fa_spec = importlib.util.spec_from_file_location("focus_audio", fa__TARGET_PATH)
if fa_spec and fa_spec.loader:
    focus_audio = importlib.util.module_from_spec(fa_spec)
    sys.modules[fa_spec.name] = focus_audio
    fa_spec.loader.exec_module(focus_audio)
else:
    raise ImportError(f"Could not load module from {fa__TARGET_PATH}")

class fa_TestFocusAudio(unittest.TestCase):
    """Test suite for offline ambient soundscapes and binaural beat PCM synthesis."""

    def test_available_presets(self):
        presets = focus_audio.AVAILABLE_PRESETS
        self.assertIn("pink_noise", presets)
        self.assertIn("brown_noise", presets)
        self.assertIn("white_noise", presets)
        self.assertIn("rain", presets)
        self.assertIn("ocean", presets)
        self.assertIn("binaural_alpha", presets)
        self.assertIn("binaural_theta", presets)

        # Binaural presets must be stereo (2 channels)
        self.assertEqual(presets["binaural_alpha"].channels, 2)
        self.assertEqual(presets["binaural_theta"].channels, 2)
        # Noise presets are mono (1 channel)
        self.assertEqual(presets["pink_noise"].channels, 1)

    def test_synthesize_pcm_mono_and_stereo(self):
        synth = focus_audio.FocusAudioSynthesizer(sample_rate=22050, mock=True)

        # Mono test
        mono_pcm, mono_ch = synth.synthesize_pcm("pink_noise", duration_sec=0.5, volume_pct=50)
        self.assertEqual(mono_ch, 1)
        self.assertEqual(len(mono_pcm), int(22050 * 0.5 * 1 * 2))

        # Stereo test
        stereo_pcm, stereo_ch = synth.synthesize_pcm("binaural_alpha", duration_sec=0.5, volume_pct=50)
        self.assertEqual(stereo_ch, 2)
        self.assertEqual(len(stereo_pcm), int(22050 * 0.5 * 2 * 2))

    def test_export_wav_real_and_mock(self):
        # Real write test
        synth_real = focus_audio.FocusAudioSynthesizer(sample_rate=22050, mock=False)
        with tempfile.TemporaryDirectory(prefix="mios-focus-test-") as tmpdir:
            wav_path = os.path.join(tmpdir, "pink.wav")
            res = synth_real.export_wav("pink_noise", wav_path, duration_sec=0.2)
            self.assertEqual(res["status"], "success")
            self.assertTrue(os.path.isfile(wav_path))
            self.assertGreater(os.path.getsize(wav_path), 500)

        # Mock test
        synth_mock = focus_audio.FocusAudioSynthesizer(sample_rate=22050, mock=True)
        res_mock = synth_mock.export_wav("pink_noise", "/tmp/mock.wav", duration_sec=0.2)
        self.assertEqual(res_mock["status"], "success")
        self.assertTrue(res_mock["mock"])

    def test_play_mock(self):
        synth = focus_audio.FocusAudioSynthesizer(mock=True)
        res = synth.play("rain", duration_sec=1.0)
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["backend"], "mock")

    def test_cli_list_presets(self):
        test_args = ["focus_audio.py", "--list-presets", "--json"]
        with patch.object(sys, "argv", test_args):
            exit_code = focus_audio.main()
            self.assertEqual(exit_code, 0)

    def test_cli_synthesize_preset_mock(self):
        test_args = ["focus_audio.py", "--preset", "brown_noise", "--duration", "0.5", "--mock", "--json"]
        with patch.object(sys, "argv", test_args):
            exit_code = focus_audio.main()
            self.assertEqual(exit_code, 0)

    def test_cli_export_wav_mock(self):
        with tempfile.TemporaryDirectory(prefix="mios-focus-cli-") as tmpdir:
            out_file = os.path.join(tmpdir, "ocean.wav")
            test_args = ["focus_audio.py", "--preset", "ocean", "--out", out_file, "--mock", "--json"]
            with patch.object(sys, "argv", test_args):
                exit_code = focus_audio.main()
                self.assertEqual(exit_code, 0)

def fa_main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(fa_TestFocusAudio)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# ======================================================================
# from tests/test-ux.py  (prefix fs_)
# ======================================================================
"""Unit and integration test suite for FontScalerEngine and font_scaler CLI."""


import importlib.util
import json
import os
import sys
import unittest
from unittest.mock import patch

fs__HERE = os.path.dirname(os.path.abspath(__file__))
fs__ROOT = os.path.normpath(os.path.join(fs__HERE, ".."))
fs__TARGET_PATH = os.path.join(fs__ROOT, "usr", "libexec", "mios", "ux", "font_scaler.py")

fs_spec = importlib.util.spec_from_file_location("font_scaler", fs__TARGET_PATH)
if fs_spec and fs_spec.loader:
    font_scaler = importlib.util.module_from_spec(fs_spec)
    sys.modules[fs_spec.name] = font_scaler
    fs_spec.loader.exec_module(font_scaler)
else:
    raise ImportError(f"Could not load module from {fs__TARGET_PATH}")

class fs_TestFontScaler(unittest.TestCase):
    """Test suite for High-DPI font metric calculation and fontconfig XML generation."""

    def test_display_metrics_dataclass(self):
        m = font_scaler.DisplayMetrics(width=3840, height=2160, dpi=192.0, scale_factor=2.0)
        self.assertEqual(m.width, 3840)
        self.assertEqual(m.dpi, 192.0)
        self.assertEqual(m.scale_factor, 2.0)

    def test_detect_metrics_mock(self):
        engine = font_scaler.FontScalerEngine(mock=True)
        metrics = engine.detect_metrics()
        self.assertEqual(metrics.dpi, 192.0)
        self.assertEqual(metrics.scale_factor, 2.0)

    def test_calculate_font_config(self):
        engine = font_scaler.FontScalerEngine(mock=True)
        # 192 DPI (2x scaling)
        metrics_4k = font_scaler.DisplayMetrics(width=3840, height=2160, dpi=192.0, scale_factor=2.0)
        cfg_4k = engine.calculate_font_config(metrics_4k)
        self.assertEqual(cfg_4k.scale_factor, 2.0)
        self.assertAlmostEqual(cfg_4k.terminal_font_pt, 22.0, places=1)
        self.assertAlmostEqual(cfg_4k.desktop_font_pt, 20.0, places=1)
        self.assertEqual(cfg_4k.cursor_size_px, 48)

        # 96 DPI (1x standard)
        metrics_1080p = font_scaler.DisplayMetrics(width=1920, height=1080, dpi=96.0, scale_factor=1.0)
        cfg_1080p = engine.calculate_font_config(metrics_1080p)
        self.assertEqual(cfg_1080p.scale_factor, 1.0)
        self.assertAlmostEqual(cfg_1080p.terminal_font_pt, 11.0, places=1)
        self.assertEqual(cfg_1080p.cursor_size_px, 24)

    def test_render_fontconfig_xml(self):
        engine = font_scaler.FontScalerEngine(mock=True)
        cfg = font_scaler.ScaledFontConfig(
            dpi=192.0,
            scale_factor=2.0,
            terminal_font_pt=22.0,
            desktop_font_pt=20.0,
            code_font_pt=24.0,
            cursor_size_px=48,
            font_family="JetBrains Mono",
            text_scaling_factor=2.0,
        )
        xml = engine.render_fontconfig_xml(cfg)
        self.assertIn("<?xml version=\"1.0\"?>", xml)
        self.assertIn("<double>192.0</double>", xml)
        self.assertIn("<family>JetBrains Mono</family>", xml)
        self.assertIn("<edit name=\"antialias\" mode=\"assign\">", xml)

    def test_apply_mock(self):
        engine = font_scaler.FontScalerEngine(mock=True)
        res = engine.apply(override_dpi=144.0, override_scale=1.5)
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["scaled_config"]["dpi"], 144.0)
        self.assertTrue(res["mock"])

    def test_cli_auto_mock(self):
        test_args = ["font_scaler.py", "--auto", "--mock", "--json"]
        with patch.object(sys, "argv", test_args):
            exit_code = font_scaler.main()
            self.assertEqual(exit_code, 0)

    def test_cli_dpi_override_mock(self):
        test_args = ["font_scaler.py", "--dpi", "192", "--scale", "2.0", "--mock", "--json"]
        with patch.object(sys, "argv", test_args):
            exit_code = font_scaler.main()
            self.assertEqual(exit_code, 0)

def fs_main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(fs_TestFontScaler)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# ======================================================================
# from tests/test-ux.py  (prefix ge_)
# ======================================================================
"""Unit and integration test suite for GnomeExtensionManager and gnome_extension CLI."""


import importlib.util
import json
import os
import sys
import unittest
from unittest.mock import patch

ge__HERE = os.path.dirname(os.path.abspath(__file__))
ge__ROOT = os.path.normpath(os.path.join(ge__HERE, ".."))
ge__TARGET_PATH = os.path.join(ge__ROOT, "usr", "libexec", "mios", "ux", "gnome_extension.py")

ge_spec = importlib.util.spec_from_file_location("gnome_extension", ge__TARGET_PATH)
if ge_spec and ge_spec.loader:
    gnome_extension = importlib.util.module_from_spec(ge_spec)
    sys.modules[ge_spec.name] = gnome_extension
    ge_spec.loader.exec_module(gnome_extension)
else:
    raise ImportError(f"Could not load module from {ge__TARGET_PATH}")

class ge_TestGnomeExtension(unittest.TestCase):
    """Test suite for GNOME Shell extension metadata, stylesheet, and GJS asynchronous code generation."""

    def test_manager_init_and_palette(self):
        manager = gnome_extension.GnomeExtensionManager(mock=True)
        self.assertEqual(manager.uuid, "mios-status@mios-dev.org")
        palette = manager._get_palette()
        self.assertIn("bg", palette)
        self.assertIn("accent", palette)
        self.assertIn("cursor", palette)

    def test_render_metadata(self):
        manager = gnome_extension.GnomeExtensionManager(mock=True)
        meta = manager.render_metadata()
        self.assertEqual(meta["uuid"], "mios-status@mios-dev.org")
        self.assertEqual(meta["name"], "MiOS Agent Status")
        self.assertIn("45", meta["shell-version"])
        self.assertIn("46", meta["shell-version"])
        self.assertIn("47", meta["shell-version"])
        self.assertIn("48", meta["shell-version"])

    def test_render_stylesheet(self):
        manager = gnome_extension.GnomeExtensionManager(mock=True)
        css = manager.render_stylesheet()
        self.assertIn(".mios-status-button", css)
        self.assertIn(".mios-status-icon", css)
        self.assertIn(".mios-status-menu", css)
        self.assertIn(".mios-quick-link", css)

    def test_render_extension_js(self):
        manager = gnome_extension.GnomeExtensionManager(mock=True)
        js = manager.render_extension_js()
        self.assertIn("import Soup from 'gi://Soup';", js)
        self.assertIn("const MiOSStatusIndicator = GObject.registerClass(", js)
        self.assertIn("export default class MiOSStatusExtension extends Extension", js)
        self.assertIn("enable()", js)
        self.assertIn("disable()", js)

    def test_generate_and_validate_mock(self):
        manager = gnome_extension.GnomeExtensionManager(mock=True)
        gen_res = manager.generate()
        self.assertEqual(gen_res["status"], "success")
        self.assertEqual(len(gen_res["files"]), 3)

        val_res = manager.validate()
        self.assertEqual(val_res["status"], "valid")
        self.assertEqual(len(val_res["errors"]), 0)

    def test_install_mock(self):
        manager = gnome_extension.GnomeExtensionManager(mock=True)
        inst_res = manager.install(user_mode=True)
        self.assertEqual(inst_res["status"], "success")
        self.assertTrue(inst_res["user_mode"])

    def test_cli_generate_mock(self):
        test_args = ["gnome_extension.py", "--generate", "--mock", "--json"]
        with patch.object(sys, "argv", test_args):
            exit_code = gnome_extension.main()
            self.assertEqual(exit_code, 0)

    def test_cli_validate_mock(self):
        test_args = ["gnome_extension.py", "--validate", "--mock", "--json"]
        with patch.object(sys, "argv", test_args):
            exit_code = gnome_extension.main()
            self.assertEqual(exit_code, 0)

    def test_cli_install_mock(self):
        test_args = ["gnome_extension.py", "--install", "--user", "--mock", "--json"]
        with patch.object(sys, "argv", test_args):
            exit_code = gnome_extension.main()
            self.assertEqual(exit_code, 0)

def ge_main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(ge_TestGnomeExtension)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# ======================================================================
# from tests/test-ux.py  (prefix lw_)
# ======================================================================
"""Unit and integration test suite for LivingWallpaperEngine and living_wallpaper CLI."""


import importlib.util
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

lw__HERE = os.path.dirname(os.path.abspath(__file__))
lw__ROOT = os.path.normpath(os.path.join(lw__HERE, ".."))
lw__TARGET_PATH = os.path.join(lw__ROOT, "usr", "libexec", "mios", "ux", "living_wallpaper.py")

lw_spec = importlib.util.spec_from_file_location("living_wallpaper", lw__TARGET_PATH)
if lw_spec and lw_spec.loader:
    living_wallpaper = importlib.util.module_from_spec(lw_spec)
    sys.modules[lw_spec.name] = living_wallpaper
    lw_spec.loader.exec_module(living_wallpaper)
else:
    raise ImportError(f"Could not load module from {lw__TARGET_PATH}")

class lw_TestLivingWallpaper(unittest.TestCase):
    """Test suite for Living Wallpaper shader generation and telemetry modulation."""

    def test_hex_to_rgb_norm(self):
        r, g, b = living_wallpaper.hex_to_rgb_norm("#FFFFFF")
        self.assertAlmostEqual(r, 1.0, places=2)
        self.assertAlmostEqual(g, 1.0, places=2)
        self.assertAlmostEqual(b, 1.0, places=2)

        r0, g0, b0 = living_wallpaper.hex_to_rgb_norm("#000000")
        self.assertAlmostEqual(r0, 0.0, places=2)
        self.assertAlmostEqual(g0, 0.0, places=2)
        self.assertAlmostEqual(b0, 0.0, places=2)

        # Invalid fallback
        fb_r, fb_g, fb_b = living_wallpaper.hex_to_rgb_norm("invalid")
        self.assertEqual((fb_r, fb_g, fb_b), (0.15, 0.13, 0.38))

    def test_engine_init_and_palette(self):
        engine = living_wallpaper.LivingWallpaperEngine(mode="dynamic", fps=120, mock=True)
        self.assertEqual(engine.mode, "dynamic")
        self.assertEqual(engine.fps, 120)
        self.assertTrue(isinstance(engine.palette, dict))
        self.assertIn("bg", engine.palette)
        self.assertIn("accent", engine.palette)
        self.assertIn("cursor", engine.palette)

    def test_sample_telemetry_mock(self):
        engine = living_wallpaper.LivingWallpaperEngine(mock=True)
        telemetry = engine.sample_telemetry()
        self.assertEqual(telemetry.cpu_percent, 32.5)
        self.assertEqual(telemetry.gpu_percent, 45.0)
        self.assertEqual(telemetry.load_factor, 0.38)
        self.assertEqual(telemetry.speed_factor, 1.45)
        self.assertEqual(telemetry.dark_mode, 1.0)

    def test_generate_glsl(self):
        engine = living_wallpaper.LivingWallpaperEngine(mock=True)
        glsl = engine.generate_glsl()
        self.assertIn("#version 330 core", glsl)
        self.assertIn("uniform float u_time;", glsl)
        self.assertIn("uniform float u_load;", glsl)
        self.assertIn("c_bg", glsl)
        self.assertIn("c_accent", glsl)
        self.assertIn("fragColor =", glsl)

    def test_generate_wgsl(self):
        engine = living_wallpaper.LivingWallpaperEngine(mock=True)
        wgsl = engine.generate_wgsl()
        self.assertIn("struct Uniforms", wgsl)
        self.assertIn("@vertex", wgsl)
        self.assertIn("@fragment", wgsl)
        self.assertIn("fs_main", wgsl)

    def test_generate_html(self):
        engine = living_wallpaper.LivingWallpaperEngine(mock=True)
        html = engine.generate_html()
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("<canvas id=\"canvas\">", html)
        self.assertIn("MiOS Telemetry Wallpaper", html)
        self.assertIn("gl.createShader", html)

    def test_run_pipeline(self):
        engine = living_wallpaper.LivingWallpaperEngine(mock=True)
        res = engine.run(render_shader=True, render_wgsl=True, render_html=True)
        self.assertEqual(res["status"], "success")
        self.assertGreater(res["glsl_lines"], 10)
        self.assertGreater(res["wgsl_lines"], 10)
        self.assertGreater(res["html_lines"], 10)
        self.assertTrue(res["mock"])

    def test_cli_render_shader_mock(self):
        test_args = ["living_wallpaper.py", "--render-shader", "--mock", "--json"]
        with patch.object(sys, "argv", test_args):
            exit_code = living_wallpaper.main()
            self.assertEqual(exit_code, 0)

    def test_cli_telemetry_mock(self):
        test_args = ["living_wallpaper.py", "--telemetry", "--mock", "--json"]
        with patch.object(sys, "argv", test_args):
            exit_code = living_wallpaper.main()
            self.assertEqual(exit_code, 0)

    def test_cli_generate_html_mock(self):
        test_args = ["living_wallpaper.py", "--generate-html", "--render-wgsl", "--mock", "--json"]
        with patch.object(sys, "argv", test_args):
            exit_code = living_wallpaper.main()
            self.assertEqual(exit_code, 0)

def lw_main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(lw_TestLivingWallpaper)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# ======================================================================
# from tests/test-ux.py  (prefix nd_)
# ======================================================================
"""Unit and integration test suite for NotificationDaemonEngine and notification_daemon CLI."""


import importlib.util
import json
import os
import sys
import unittest
from unittest.mock import patch

nd__HERE = os.path.dirname(os.path.abspath(__file__))
nd__ROOT = os.path.normpath(os.path.join(nd__HERE, ".."))
nd__TARGET_PATH = os.path.join(nd__ROOT, "usr", "libexec", "mios", "ux", "notification_daemon.py")

nd_spec = importlib.util.spec_from_file_location("notification_daemon", nd__TARGET_PATH)
if nd_spec and nd_spec.loader:
    notification_daemon = importlib.util.module_from_spec(nd_spec)
    sys.modules[nd_spec.name] = notification_daemon
    nd_spec.loader.exec_module(notification_daemon)
else:
    raise ImportError(f"Could not load module from {nd__TARGET_PATH}")

class nd_TestNotificationDaemon(unittest.TestCase):
    """Test suite for desktop notifications, rate limiting, and HITL alert dispatching."""

    def test_notification_message_dataclass(self):
        msg = notification_daemon.NotificationMessage(
            title="Bake Staged",
            body="3 changes approved",
            severity="critical",
            actions=["Approve", "Reject"],
        )
        self.assertEqual(msg.title, "Bake Staged")
        self.assertEqual(msg.severity, "critical")
        self.assertEqual(len(msg.actions), 2)

    def test_engine_send_mock(self):
        engine = notification_daemon.NotificationDaemonEngine(mock=True)
        msg = notification_daemon.NotificationMessage(title="Test Alert", body="Details")
        res = engine.send(msg)
        self.assertTrue(res["sent"])
        self.assertEqual(res["backend"], "mock_toast")
        self.assertIn("notification_id", res)
        self.assertEqual(len(engine.history), 1)

    def test_rate_limiter_enforcement(self):
        engine = notification_daemon.NotificationDaemonEngine(rate_limit_per_min=2, mock=True)
        msg = notification_daemon.NotificationMessage(title="Spam Test")
        res1 = engine.send(msg)
        self.assertTrue(res1["sent"])
        res2 = engine.send(msg)
        self.assertTrue(res2["sent"])
        res3 = engine.send(msg)
        self.assertFalse(res3["sent"])
        self.assertEqual(res3["reason"], "rate_limited")

    def test_run_daemon_loop_mock(self):
        engine = notification_daemon.NotificationDaemonEngine(mock=True)
        events = engine.run_daemon_loop(max_ticks=1)
        self.assertEqual(len(events), 1)
        self.assertTrue(events[0]["sent"])
        self.assertEqual(events[0]["message"]["severity"], "critical")

    def test_cli_send_mock(self):
        test_args = ["notification_daemon.py", "--title", "CLI Alert", "--body", "Action required", "--mock", "--json"]
        with patch.object(sys, "argv", test_args):
            exit_code = notification_daemon.main()
            self.assertEqual(exit_code, 0)

    def test_cli_listen_mock(self):
        test_args = ["notification_daemon.py", "--listen", "--mock", "--json"]
        with patch.object(sys, "argv", test_args):
            exit_code = notification_daemon.main()
            self.assertEqual(exit_code, 0)

    def test_cli_post_alias_mock(self):
        test_args = ["notification_daemon.py", "--post", "Quick message", "--level", "warn", "--mock", "--json"]
        with patch.object(sys, "argv", test_args):
            exit_code = notification_daemon.main()
            self.assertEqual(exit_code, 0)

def nd_main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(nd_TestNotificationDaemon)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# ======================================================================
# from tests/test-ux.py  (prefix sb_)
# ======================================================================
"""Unit and integration test suite for StatusBarEngine and status_bar CLI."""


import importlib.util
import json
import os
import sys
import unittest
from unittest.mock import patch

sb__HERE = os.path.dirname(os.path.abspath(__file__))
sb__ROOT = os.path.normpath(os.path.join(sb__HERE, ".."))
sb__TARGET_PATH = os.path.join(sb__ROOT, "usr", "libexec", "mios", "ux", "status_bar.py")

sb_spec = importlib.util.spec_from_file_location("status_bar", sb__TARGET_PATH)
if sb_spec and sb_spec.loader:
    status_bar = importlib.util.module_from_spec(sb_spec)
    sys.modules[sb_spec.name] = status_bar
    sb_spec.loader.exec_module(status_bar)
else:
    raise ImportError(f"Could not load module from {sb__TARGET_PATH}")

class sb_TestStatusBar(unittest.TestCase):
    """Test suite for AI brain status bar streaming and QML component generation."""

    def test_status_bar_state_dataclass(self):
        state = status_bar.StatusBarState(
            model_id="Qwen2.5-Coder-7B",
            agent_status="thinking",
            token_rate_tps=42.5,
        )
        self.assertEqual(state.model_id, "Qwen2.5-Coder-7B")
        self.assertEqual(state.agent_status, "thinking")
        self.assertEqual(state.token_rate_tps, 42.5)

    def test_engine_init_and_palette(self):
        engine = status_bar.StatusBarEngine(mock=True)
        self.assertTrue(engine.mock)
        self.assertIn("bg", engine.palette)
        self.assertIn("accent", engine.palette)
        self.assertIn("success", engine.palette)

    def test_fetch_snapshot_mock(self):
        engine = status_bar.StatusBarEngine(mock=True)
        snapshot = engine.fetch_snapshot()
        self.assertEqual(snapshot.model_id, "Qwen2.5-Coder-7B-Instruct-GGUF")
        self.assertEqual(snapshot.agent_status, "deliberating")
        self.assertEqual(snapshot.token_rate_tps, 34.8)
        self.assertEqual(snapshot.vram_used_mb, 5640)
        self.assertEqual(snapshot.vram_total_mb, 16384)
        self.assertTrue(snapshot.endpoint_healthy)

    def test_generate_qml(self):
        engine = status_bar.StatusBarEngine(mock=True)
        qml = engine.generate_qml()
        self.assertIn("import QtQuick 2.15", qml)
        self.assertIn("import QtQuick.Layouts 1.15", qml)
        self.assertIn("Rectangle {", qml)
        self.assertIn("property string modelName:", qml)
        self.assertIn("property string agentStatus:", qml)
        self.assertIn("property real tokenRate:", qml)

    def test_run_stream(self):
        engine = status_bar.StatusBarEngine(mock=True)
        snaps = engine.run_stream(interval_sec=0.01, max_iterations=2)
        self.assertEqual(len(snaps), 2)
        self.assertEqual(snaps[0]["model_id"], "Qwen2.5-Coder-7B-Instruct-GGUF")

    def test_cli_snapshot_mock(self):
        test_args = ["status_bar.py", "--snapshot", "--mock", "--json"]
        with patch.object(sys, "argv", test_args):
            exit_code = status_bar.main()
            self.assertEqual(exit_code, 0)

    def test_cli_stream_mock(self):
        test_args = ["status_bar.py", "--stream", "--count", "2", "--interval", "0.01", "--mock", "--json"]
        with patch.object(sys, "argv", test_args):
            exit_code = status_bar.main()
            self.assertEqual(exit_code, 0)

    def test_cli_generate_qml_mock(self):
        test_args = ["status_bar.py", "--generate-qml", "AiStatus.qml", "--mock", "--json"]
        with patch.object(sys, "argv", test_args):
            exit_code = status_bar.main()
            self.assertEqual(exit_code, 0)

def sb_main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(sb_TestStatusBar)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# ======================================================================
# from tests/test-ux.py  (prefix ts_)
# ======================================================================
"""Unit and integration test suite for ThemeSyncEngine and theme_sync CLI."""


import importlib.util
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ts__HERE = os.path.dirname(os.path.abspath(__file__))
ts__ROOT = os.path.normpath(os.path.join(ts__HERE, ".."))
ts__TARGET_PATH = os.path.join(ts__ROOT, "usr", "libexec", "mios", "ux", "theme_sync.py")

ts_spec = importlib.util.spec_from_file_location("theme_sync", ts__TARGET_PATH)
if ts_spec and ts_spec.loader:
    theme_sync = importlib.util.module_from_spec(ts_spec)
    sys.modules[ts_spec.name] = theme_sync
    ts_spec.loader.exec_module(theme_sync)
else:
    raise ImportError(f"Could not load module from {ts__TARGET_PATH}")

class ts_TestThemeSync(unittest.TestCase):
    """Test suite for Windows Registry, GTK3, and GTK4 theme generation and synchronization."""

    def test_hex_to_dword_conversions(self):
        # 0x00BBGGRR for Windows Console
        # Blue=#1A407F -> r=0x1A, g=0x40, b=0x7F -> (0x7F << 16) | (0x40 << 8) | 0x1A
        bgr = theme_sync.hex_to_dword_bgr("#1A407F")
        self.assertEqual(bgr, (0x7F << 16) | (0x40 << 8) | 0x1A)

        # 0xAABBGGRR for Windows DWM
        abgr = theme_sync.hex_to_dword_abgr("#1A407F", alpha=0xFF)
        self.assertEqual(abgr, (0xFF << 24) | (0x7F << 16) | (0x40 << 8) | 0x1A)

    def test_engine_init_and_palette(self):
        engine = theme_sync.ThemeSyncEngine(target="all", dark_mode=True, mock=True)
        self.assertEqual(engine.target, "all")
        self.assertTrue(engine.dark_mode)
        self.assertIn("bg", engine.palette)
        self.assertIn("accent", engine.palette)
        self.assertIn("ansi_0_black", engine.palette)

    def test_generate_windows_reg(self):
        engine = theme_sync.ThemeSyncEngine(mock=True)
        reg = engine.generate_windows_reg()
        self.assertIn("Windows Registry Editor Version 5.00", reg)
        self.assertIn("[HKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\CurrentVersion\\Themes\\Personalize]", reg)
        self.assertIn("[HKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\DWM]", reg)
        self.assertIn("[HKEY_CURRENT_USER\\Console\\MiOS]", reg)
        self.assertIn('"ColorTable00"=', reg)
        self.assertIn('"ColorTable15"=', reg)

    def test_generate_gtk3_css(self):
        engine = theme_sync.ThemeSyncEngine(mock=True)
        css = engine.generate_gtk3_css()
        self.assertIn("/* MiOS GTK3 Theme Definitions", css)
        self.assertIn("@define-color mios_bg", css)
        self.assertIn("@define-color mios_accent", css)
        self.assertIn("@define-color theme_bg_color", css)
        self.assertIn("window {", css)

    def test_generate_gtk4_css(self):
        engine = theme_sync.ThemeSyncEngine(mock=True)
        css = engine.generate_gtk4_css()
        self.assertIn("/* MiOS GTK4 Theme Definitions", css)
        self.assertIn(":root {", css)
        self.assertIn("--mios-bg:", css)
        self.assertIn("--accent-color:", css)
        self.assertIn("--window-bg-color:", css)

    def test_run_sync_pipeline(self):
        engine = theme_sync.ThemeSyncEngine(mock=True)
        res = engine.run()
        self.assertEqual(res["status"], "success")
        self.assertGreater(res["windows_reg_lines"], 10)
        self.assertGreater(res["gtk3_lines"], 10)
        self.assertGreater(res["gtk4_lines"], 10)
        self.assertTrue(res["mock"])

    def test_cli_sync_mock(self):
        test_args = ["theme_sync.py", "--sync", "--mock", "--json"]
        with patch.object(sys, "argv", test_args):
            exit_code = theme_sync.main()
            self.assertEqual(exit_code, 0)

    def test_cli_target_windows_mock(self):
        test_args = ["theme_sync.py", "--target", "windows", "--mock", "--json"]
        with patch.object(sys, "argv", test_args):
            exit_code = theme_sync.main()
            self.assertEqual(exit_code, 0)

    def test_cli_target_gtk_mock(self):
        test_args = ["theme_sync.py", "--target", "gtk3", "--mock", "--json"]
        with patch.object(sys, "argv", test_args):
            exit_code = theme_sync.main()
            self.assertEqual(exit_code, 0)

def ts_main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(ts_TestThemeSync)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# ======================================================================
# from tests/test-ux.py  (prefix tt_)
# ======================================================================
"""Unit and integration test suite for TmuxThemeEngine and tmux_theme CLI."""


import importlib.util
import json
import os
import sys
import unittest
from unittest.mock import patch

tt__HERE = os.path.dirname(os.path.abspath(__file__))
tt__ROOT = os.path.normpath(os.path.join(tt__HERE, ".."))
tt__TARGET_PATH = os.path.join(tt__ROOT, "usr", "libexec", "mios", "ux", "tmux_theme.py")

if os.path.isfile(tt__TARGET_PATH):
    tt_spec = importlib.util.spec_from_file_location("tmux_theme", tt__TARGET_PATH)
    if tt_spec and tt_spec.loader:
        tmux_theme = importlib.util.module_from_spec(tt_spec)
        sys.modules[tt_spec.name] = tmux_theme
        tt_spec.loader.exec_module(tmux_theme)
    else:
        tmux_theme = None
else:
    tmux_theme = None

@unittest.skipIf(tmux_theme is None, "tmux_theme.py was strangler-deleted (ported to native mios-gen render-tmux-theme)")
class tt_TestTmuxTheme(unittest.TestCase):
    """Test suite for tmux theme rendering across powerline, rounded, and minimal styles."""

    def test_engine_init_and_palette(self):
        engine = tmux_theme.TmuxThemeEngine(style="powerline", status_position="top", mock=True)
        self.assertEqual(engine.style, "powerline")
        self.assertEqual(engine.status_position, "top")
        self.assertIn("bg", engine.palette)
        self.assertIn("accent", engine.palette)
        self.assertIn("cursor", engine.palette)

    def test_generate_powerline_config(self):
        engine = tmux_theme.TmuxThemeEngine(style="powerline", mock=True,
                                          data=tmux_theme.mios_toml.vendor_tree(tt__ROOT))
        cfg = engine.generate_config()
        self.assertIn("# MiOS Canonical Tmux Theme", cfg)
        self.assertIn("set -g status on", cfg)
        expected_pane_bg = "default" if engine.pane_background == "terminal" else engine.palette["bg"]
        self.assertIn(f'set -g window-style "bg={expected_pane_bg},fg={engine.palette["fg"]}"', cfg)
        self.assertIn("set -g pane-active-border-style", cfg)
        self.assertIn(engine.data["theme"]["prompt"]["powerline_right"], cfg)
        self.assertIn(engine.data["theme"]["prompt"]["powerline_left"], cfg)

    def test_generate_rounded_config(self):
        engine = tmux_theme.TmuxThemeEngine(style="rounded", mock=True)
        cfg = engine.generate_config()
        self.assertIn("", cfg)
        self.assertIn("", cfg)

    def test_pane_background_inherits_terminal_or_uses_ssot_color(self):
        import copy
        data = copy.deepcopy(tmux_theme.mios_toml.vendor_tree(tt__ROOT))
        data["colors"]["bg"] = "#123456"
        data["theme"]["tmux"]["pane_background"] = "terminal"
        cfg = tmux_theme.TmuxThemeEngine(data=data).generate_config()
        self.assertIn('set -g window-active-style "bg=default,', cfg)
        data["theme"]["tmux"]["pane_background"] = "theme"
        cfg = tmux_theme.TmuxThemeEngine(data=data).generate_config()
        self.assertIn('set -g window-active-style "bg=#123456,', cfg)
        data["theme"]["tmux"]["pane_background"] = "DEVLOOP-PLANTED-INVALID"
        with self.assertRaisesRegex(ValueError, "pane_background"):
            tmux_theme.TmuxThemeEngine(data=data)

    def test_status_background_inherits_terminal_or_acrylic(self):
        import copy
        data = copy.deepcopy(tmux_theme.mios_toml.vendor_tree(tt__ROOT))
        data["colors"]["bg"] = "#123456"
        data["theme"]["acrylic"] = True
        data["theme"]["opacity"] = 50
        cfg = tmux_theme.TmuxThemeEngine(data=data).generate_config()
        self.assertIn('set -g status-style "bg=default,', cfg)
        data["theme"]["tmux"]["status_background"] = "theme"
        cfg = tmux_theme.TmuxThemeEngine(data=data).generate_config()
        self.assertIn('set -g status-style "bg=#123456,', cfg)
        data["theme"]["tmux"]["status_background"] = "DEVLOOP-PLANTED-INVALID"
        with self.assertRaisesRegex(ValueError, "status_background"):
            tmux_theme.TmuxThemeEngine(data=data)

    def test_ssot_layout_and_ascii_font_fallback(self):
        import copy
        data = copy.deepcopy(tmux_theme.mios_toml.vendor_tree(tt__ROOT))
        data["theme"]["tmux"].update(status_position="top", status_interval_s=7)
        data["theme"]["font"]["family"] = "SSH Plain Mono"
        cfg = tmux_theme.TmuxThemeEngine(data=data).generate_config()
        self.assertIn("set -g status-position top", cfg)
        self.assertIn("set -g status-interval 7", cfg)
        self.assertIn("# Minimal Status Line Formatting", cfg)
        self.assertNotIn("", cfg)
        data["theme"]["tmux"]["glyph_mode"] = "DEVLOOP-PLANTED-INVALID"
        with self.assertRaisesRegex(ValueError, "glyph_mode"):
            tmux_theme.TmuxThemeEngine(data=data)

    def test_runtime_projection_changes_with_ssot_and_rejects_collision(self):
        import copy, tempfile, subprocess
        data = copy.deepcopy(tmux_theme.mios_toml.vendor_tree(tt__ROOT))
        data["colors"]["bg"] = "#123456"
        data["theme"]["tmux"]["status_position"] = "top"
        data["keybindings"]["actions"][0]["key"] = "u"
        with tempfile.TemporaryDirectory() as directory:
            tmux_theme.project_runtime(directory, data)
            with open(os.path.join(directory, "tmux.conf"), encoding="utf-8") as handle:
                config = handle.read()
            self.assertIn("bg=#123456", config)
            self.assertIn("set -g status-position top", config)
            self.assertIn("bind-key u new-window", config)
            with open(os.path.join(directory, "mios.omp.json"), encoding="utf-8") as handle:
                prompt = handle.read()
            self.assertIn("#123456", prompt)
            data["keybindings"]["actions"][1]["key"] = "u"
            with self.assertRaises(subprocess.CalledProcessError) as raised:
                tmux_theme.project_runtime(directory, data)
            self.assertIn("duplicate or non-mobile key: u", raised.exception.stderr)
            with open(os.path.join(directory, "tmux.conf"), encoding="utf-8") as handle:
                self.assertEqual(handle.read(), config)
            with open(os.path.join(directory, "mios.omp.json"), encoding="utf-8") as handle:
                self.assertEqual(handle.read(), prompt)

    def test_mobile_prompt_and_tmux_drop_font_dependencies(self):
        data = tmux_theme.mios_toml.vendor_tree(tt__ROOT)
        data["theme"]["tmux"]["remote_glyph_mode"] = "auto"
        data["theme"]["prompt"]["remote_glyph_mode"] = "auto"
        with patch.dict(os.environ, {"SSH_CONNECTION": "127.0.0.1 5000 127.0.0.1 22"}):
            config = tmux_theme.TmuxThemeEngine(data=data).generate_config()
            self.assertNotIn("", config)
            prompt = json.loads(tmux_theme.render_prompt(data, remote=True))
        segments = [s for b in prompt["blocks"] for s in b["segments"]]
        self.assertTrue(all(s["style"] == "plain" for s in segments))
        self.assertTrue(all(s["template"].isascii() for s in segments))
        self.assertEqual(segments[-1]["template"], data["theme"]["prompt"]["ascii"]["closer"])
        self.assertEqual(segments[1]["background"], data["colors"]["accent"])
        data["theme"]["tmux"]["remote_glyph_mode"] = "nerd"
        data["theme"]["prompt"]["remote_glyph_mode"] = "nerd"
        with patch.dict(os.environ, {"SSH_CONNECTION": "127.0.0.1 5000 127.0.0.1 22"}):
            self.assertIn("", tmux_theme.TmuxThemeEngine(data=data).generate_config())
        self.assertEqual(tmux_theme.render_prompt(data, remote=True), tmux_theme.render_prompt(data))

    def test_generate_minimal_config(self):
        engine = tmux_theme.TmuxThemeEngine(style="minimal", mock=True)
        cfg = engine.generate_config()
        self.assertIn("# Minimal Status Line Formatting", cfg)
        self.assertIn("set -g status-left", cfg)

    def test_run_pipeline(self):
        engine = tmux_theme.TmuxThemeEngine(mock=True)
        res = engine.run()
        self.assertEqual(res["status"], "success")
        self.assertGreater(res["config_lines"], 15)
        self.assertTrue(res["mock"])

    def test_cli_render_powerline_mock(self):
        test_args = ["tmux_theme.py", "--render", "--style", "powerline", "--mock", "--json"]
        with patch.object(sys, "argv", test_args):
            exit_code = tmux_theme.main()
            self.assertEqual(exit_code, 0)

    def test_cli_render_rounded_mock(self):
        test_args = ["tmux_theme.py", "--render", "--style", "rounded", "--mock", "--json"]
        with patch.object(sys, "argv", test_args):
            exit_code = tmux_theme.main()
            self.assertEqual(exit_code, 0)

    def test_cli_render_minimal_mock(self):
        test_args = ["tmux_theme.py", "--render", "--style", "minimal", "--mock", "--json"]
        with patch.object(sys, "argv", test_args):
            exit_code = tmux_theme.main()
            self.assertEqual(exit_code, 0)

    def test_installed_theme_golden_both_sides(self):
        import contextlib, io, tempfile
        def cli(*args):
            with patch.object(sys, "argv", ["tmux_theme.py", *args]):
                return tmux_theme.main()
        self.assertEqual(cli("--check-fixture", tt__ROOT), 0)
        with open(os.path.join(tt__ROOT, "usr/share/mios/tmux/blink-mobile-keys.tmux.conf"), encoding="utf-8") as fh:
            self.assertIn(f"source-file /{tmux_theme.GOLDEN}\n", fh.read())
        with tempfile.TemporaryDirectory(prefix="mios-test-tt-") as tmp:
            self.assertEqual(cli("--write-fixture", tmp), 0)
            path = os.path.join(tmp, tmux_theme.GOLDEN)
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text.rstrip("\n"))
            self.assertEqual(tmux_theme.mios_toml.golden_diff(path, text), f"{path}: differs from the generator only in line endings")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text.replace("set -g status on\n", "set -g status off\n"))
            with contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(cli("--check-fixture", tmp), 1)
            self.assertIn("mios-theme.tmux.conf:", err.getvalue())
            self.assertIn("'set -g status off'", err.getvalue())

def tt_main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(tt_TestTmuxTheme)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# ======================================================================
# from tests/test-ux.py  (prefix wot_)
# ======================================================================
"""
Automated unit, frame pacing benchmark, and IPC telemetry test suite for
the MiOS Living Wallpaper Occlusion Engine (mios-wallpaperd).

Validates:
1. Active rendering at 60 FPS with < 2% GPU load (nominal 1.8%) when desktop is visible.
2. Suspension to 0 FPS with 0.0% GPU load when desktop is occluded by open windows.
3. Low-priority Vulkan compute queue scheduling (VK_QUEUE_GLOBAL_PRIORITY_LOW_EXT).
4. Uniform and telemetry IPC socket listener communication over Unix domain socket.
5. CLI controls (--status, --json, --set-occluded, --mock, --daemon, --socket).
6. Systemd user service unit specification compliance.
"""


import importlib.util
import io
import json
import os
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

# Load module dynamically
wot__HERE = os.path.dirname(os.path.abspath(__file__))
wot__ROOT = os.path.normpath(os.path.join(wot__HERE, ".."))
wot__TARGET_PATH = os.path.join(wot__ROOT, "usr", "libexec", "mios", "ux", "wallpaperd.py")
wot__SERVICE_PATH = os.path.join(wot__ROOT, "usr", "lib", "systemd", "user", "mios-wallpaper.service")

wot_spec = importlib.util.spec_from_file_location("wallpaperd", wot__TARGET_PATH)
if wot_spec and wot_spec.loader:
    wallpaperd = importlib.util.module_from_spec(wot_spec)
    sys.modules[wot_spec.name] = wallpaperd
    wot_spec.loader.exec_module(wallpaperd)
else:
    raise ImportError(f"Could not load module from {wot__TARGET_PATH}")

class wot_TestWallpaperOcclusionThrottle(unittest.TestCase):
    """Test suite for living wallpaper occlusion throttling, frame pacing, and IPC."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.sock_path = os.path.join(self.temp_dir.name, "test-wallpaper.sock")

    def tearDown(self):
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_01_visible_state_frame_pacing_and_gpu_load(self):
        """Verify 60 FPS rendering with <2% GPU load (nominal 1.8%) when desktop is visible."""
        engine = wallpaperd.WallpaperDaemonEngine(
            fps=60,
            mode="ambient",
            socket_path=self.sock_path,
            mock=True,
            initial_occluded=False,
        )

        status = engine.get_status()
        self.assertTrue(status["rendering"], "Engine should be actively rendering when visible")
        self.assertEqual(status["fps"], 60, "Framerate should target 60 FPS")
        self.assertAlmostEqual(status["gpu_load_pct"], 1.8, delta=0.2, msg="GPU load should be nominal 1.8%")
        self.assertLess(status["gpu_load_pct"], 2.0, "GPU load must remain < 2.0% for AI inference headroom")
        self.assertFalse(status["occluded"], "Desktop occlusion flag should be False")
        self.assertEqual(status["vulkan_queue_priority"], "VK_QUEUE_GLOBAL_PRIORITY_LOW_EXT")

        # Step 60 frames and verify duty cycle
        for i in range(60):
            res = engine.step_frame(delta_time=0.01667)
            self.assertTrue(res["rendered"])
            self.assertEqual(res["fps"], 60)
            self.assertAlmostEqual(res["gpu_load_pct"], 1.8, delta=0.2)
            self.assertFalse(res["occluded"])

        self.assertEqual(engine.vulkan_queue.rendered_frame_count, 60)
        self.assertEqual(engine.vulkan_queue.suspended_frame_count, 0)
        self.assertGreater(engine.vulkan_queue.total_duty_time_s, 0.0)

    def test_02_occluded_state_throttle_to_zero_fps(self):
        """Verify rendering suspends to 0 FPS and 0.0% GPU load on window occlusion."""
        engine = wallpaperd.WallpaperDaemonEngine(
            fps=60,
            mode="ambient",
            socket_path=self.sock_path,
            mock=True,
            initial_occluded=False,
        )

        # Trigger window occlusion
        updated_status = engine.set_occluded(True)
        self.assertFalse(updated_status["rendering"], "Rendering must be False when occluded")
        self.assertEqual(updated_status["fps"], 0, "Framerate must drop to 0 FPS when occluded")
        self.assertEqual(updated_status["gpu_load_pct"], 0.0, "GPU load must drop to 0.0% when occluded")
        self.assertTrue(updated_status["occluded"], "Occlusion flag must be True")

        # Step 60 ticks in occluded state
        for _ in range(60):
            res = engine.step_frame(delta_time=0.01667)
            self.assertFalse(res["rendered"])
            self.assertEqual(res["fps"], 0)
            self.assertEqual(res["gpu_load_pct"], 0.0)
            self.assertEqual(res["duty_cycle"], 0.0)
            self.assertTrue(res["occluded"])

        self.assertEqual(engine.vulkan_queue.suspended_frame_count, 60)
        self.assertEqual(engine.vulkan_queue.rendered_frame_count, 0)

    def test_03_resume_visible_after_occlusion(self):
        """Verify seamless transition between occluded (0 FPS) and visible (60 FPS)."""
        engine = wallpaperd.WallpaperDaemonEngine(
            fps=60,
            mode="ambient",
            socket_path=self.sock_path,
            mock=True,
            initial_occluded=True,
        )

        # Initial occluded
        st1 = engine.get_status()
        self.assertFalse(st1["rendering"])
        self.assertEqual(st1["fps"], 0)
        self.assertEqual(st1["gpu_load_pct"], 0.0)

        # Transition to visible
        st2 = engine.set_occluded(False)
        self.assertTrue(st2["rendering"])
        self.assertEqual(st2["fps"], 60)
        self.assertAlmostEqual(st2["gpu_load_pct"], 1.8, delta=0.2)

        # Transition back to occluded
        st3 = engine.set_occluded(True)
        self.assertFalse(st3["rendering"])
        self.assertEqual(st3["fps"], 0)
        self.assertEqual(st3["gpu_load_pct"], 0.0)

    def test_04_vulkan_compute_priority_queue(self):
        """Verify Vulkan compute queue priority is low-priority to yield to AI models."""
        queue = wallpaperd.VulkanComputeQueue(
            priority="VK_QUEUE_GLOBAL_PRIORITY_LOW_EXT",
            target_fps=60,
            nominal_gpu_load=1.8,
        )
        self.assertEqual(queue.priority, "VK_QUEUE_GLOBAL_PRIORITY_LOW_EXT")

        # Step frame when visible
        f1 = queue.render_frame(occluded=False, delta_time=0.01667)
        self.assertTrue(f1["rendered"])
        self.assertEqual(f1["queue_priority"], "VK_QUEUE_GLOBAL_PRIORITY_LOW_EXT")

        # Step frame when occluded
        f2 = queue.render_frame(occluded=True, delta_time=0.01667)
        self.assertFalse(f2["rendered"])
        self.assertEqual(f2["fps"], 0)
        self.assertEqual(f2["gpu_load_pct"], 0.0)

    def test_05_telemetry_socket_server_and_ipc_commands(self):
        """Verify Unix domain socket IPC communication, command dispatching, and status retrieval."""
        engine = wallpaperd.WallpaperDaemonEngine(
            fps=60,
            mode="ambient",
            socket_path=self.sock_path,
            mock=True,
        )

        started = engine.start_socket_server()
        self.assertTrue(started, "Socket server should start successfully")

        try:
            # Test ping command
            ping_resp = wallpaperd.send_socket_command(self.sock_path, {"cmd": "ping"})
            self.assertIsNotNone(ping_resp)
            self.assertEqual(ping_resp.get("status"), "ok")
            self.assertTrue(ping_resp.get("pong"))

            # Test status command
            status_resp = wallpaperd.send_socket_command(self.sock_path, {"cmd": "status"})
            self.assertIsNotNone(status_resp)
            self.assertTrue(status_resp.get("rendering"))
            self.assertEqual(status_resp.get("fps"), 60)
            self.assertAlmostEqual(status_resp.get("gpu_load_pct"), 1.8, delta=0.2)
            self.assertFalse(status_resp.get("occluded"))

            # Test set_occluded true over IPC socket
            occ_resp = wallpaperd.send_socket_command(
                self.sock_path,
                {"cmd": "set_occluded", "occluded": True},
            )
            self.assertIsNotNone(occ_resp)
            self.assertFalse(occ_resp.get("rendering"))
            self.assertEqual(occ_resp.get("fps"), 0)
            self.assertEqual(occ_resp.get("gpu_load_pct"), 0.0)
            self.assertTrue(occ_resp.get("occluded"))

            # Test set_occluded false over IPC socket
            vis_resp = wallpaperd.send_socket_command(
                self.sock_path,
                {"cmd": "set_occluded", "occluded": False},
            )
            self.assertIsNotNone(vis_resp)
            self.assertTrue(vis_resp.get("rendering"))
            self.assertEqual(vis_resp.get("fps"), 60)
            self.assertAlmostEqual(vis_resp.get("gpu_load_pct"), 1.8, delta=0.2)
            self.assertFalse(vis_resp.get("occluded"))

            # Test uniform update over IPC socket
            uniform_resp = wallpaperd.send_socket_command(
                self.sock_path,
                {"cmd": "uniforms", "data": {"cpu_percent": 45.0, "ai_inference_tps": 32.1}},
            )
            self.assertIsNotNone(uniform_resp)
            self.assertEqual(uniform_resp.get("status"), "ok")
            self.assertEqual(engine.uniforms.cpu_percent, 45.0)
            self.assertEqual(engine.uniforms.ai_inference_tps, 32.1)
        finally:
            engine.stop_socket_server()

    def test_06_mock_mode_and_telemetry_uniforms(self):
        """Verify telemetry uniform container and mock mode execution."""
        engine = wallpaperd.WallpaperDaemonEngine(mock=True)
        engine.update_uniforms({"cpu_percent": 28.5, "gpu_percent": 1.2, "speed_factor": 1.35})
        self.assertEqual(engine.uniforms.cpu_percent, 28.5)
        self.assertEqual(engine.uniforms.gpu_percent, 1.2)
        self.assertEqual(engine.uniforms.speed_factor, 1.35)

        st = engine.get_status()
        self.assertTrue(st["mock"])
        self.assertEqual(st["mode"], "ambient")

    def test_07_cli_status_json_mock(self):
        """Verify CLI execution of `wallpaperd.py --status --json --mock`."""
        test_args = ["wallpaperd.py", "--status", "--json", "--mock", "--socket", self.sock_path]
        buf = io.StringIO()
        with patch.object(sys, "argv", test_args), patch("sys.stdout", buf):
            exit_code = wallpaperd.main()
            self.assertEqual(exit_code, 0)

        output = buf.getvalue().strip()
        data = json.loads(output)
        self.assertIn("rendering", data)
        self.assertIn("fps", data)
        self.assertIn("gpu_load_pct", data)
        self.assertIn("occluded", data)
        self.assertTrue(data["rendering"])
        self.assertEqual(data["fps"], 60)
        self.assertAlmostEqual(data["gpu_load_pct"], 1.8, delta=0.2)
        self.assertFalse(data["occluded"])

    def test_08_cli_set_occluded_json_mock(self):
        """Verify CLI execution of `wallpaperd.py --set-occluded true|false --json --mock`."""
        # Test occluded true
        test_args_true = [
            "wallpaperd.py",
            "--set-occluded", "true",
            "--json",
            "--mock",
            "--socket", self.sock_path,
        ]
        buf_true = io.StringIO()
        with patch.object(sys, "argv", test_args_true), patch("sys.stdout", buf_true):
            code_true = wallpaperd.main()
            self.assertEqual(code_true, 0)

        data_true = json.loads(buf_true.getvalue().strip())
        self.assertFalse(data_true["rendering"])
        self.assertEqual(data_true["fps"], 0)
        self.assertEqual(data_true["gpu_load_pct"], 0.0)
        self.assertTrue(data_true["occluded"])

        # Test occluded false
        test_args_false = [
            "wallpaperd.py",
            "--set-occluded", "false",
            "--json",
            "--mock",
            "--socket", self.sock_path,
        ]
        buf_false = io.StringIO()
        with patch.object(sys, "argv", test_args_false), patch("sys.stdout", buf_false):
            code_false = wallpaperd.main()
            self.assertEqual(code_false, 0)

        data_false = json.loads(buf_false.getvalue().strip())
        self.assertTrue(data_false["rendering"])
        self.assertEqual(data_false["fps"], 60)
        self.assertAlmostEqual(data_false["gpu_load_pct"], 1.8, delta=0.2)
        self.assertFalse(data_false["occluded"])

    def test_09_cli_daemon_iterations_mock(self):
        """Verify CLI execution of `wallpaperd.py --daemon --mock --iterations 10 --json`."""
        test_args = [
            "wallpaperd.py",
            "--daemon",
            "--mock",
            "--iterations", "10",
            "--json",
            "--socket", self.sock_path,
        ]
        buf = io.StringIO()
        with patch.object(sys, "argv", test_args), patch("sys.stdout", buf):
            code = wallpaperd.main()
            self.assertEqual(code, 0)

        output = buf.getvalue().strip()
        data = json.loads(output)
        self.assertTrue(data.get("daemon_started"))

    def test_10_systemd_user_service_spec(self):
        """Verify systemd user service unit exists and contains required directives."""
        self.assertTrue(os.path.exists(wot__SERVICE_PATH), f"Service unit {wot__SERVICE_PATH} must exist")
        with open(wot__SERVICE_PATH, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("ExecStart=/usr/libexec/mios/ux/wallpaperd.py --daemon", content)
        self.assertIn("Restart=on-failure", content)
        self.assertIn("[Unit]", content)
        self.assertIn("[Service]", content)
        self.assertIn("[Install]", content)
        self.assertIn("graphical-session.target", content)

def wot_main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(wot_TestWallpaperOcclusionThrottle)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# ======================================================================
# from tests/test-ux.py  (prefix wcg_)
# ======================================================================
"""Unit and integration test suite for WmConfigGenEngine and wm_config_gen CLI."""


import importlib.util
import json
import os
import sys
import unittest
from unittest.mock import patch

wcg__HERE = os.path.dirname(os.path.abspath(__file__))
wcg__ROOT = os.path.normpath(os.path.join(wcg__HERE, ".."))
wcg__TARGET_PATH = os.path.join(wcg__ROOT, "usr", "libexec", "mios", "ux", "wm_config_gen.py")

wcg_spec = importlib.util.spec_from_file_location("wm_config_gen", wcg__TARGET_PATH)
if wcg_spec and wcg_spec.loader:
    wm_config_gen = importlib.util.module_from_spec(wcg_spec)
    sys.modules[wcg_spec.name] = wm_config_gen
    wcg_spec.loader.exec_module(wm_config_gen)
else:
    raise ImportError(f"Could not load module from {wcg__TARGET_PATH}")

import contextlib
import re
import io
import shutil
import tempfile
import tomllib

wcg__VENDOR = os.path.join(wcg__ROOT, "usr", "share", "mios", "mios.toml")

def wcg_env(user_toml: str = "", vendor: str = wcg__VENDOR):
    """The overlay pinned to this tree's vendor tier plus an optional user tier; no host tier."""
    return patch.dict(os.environ, {
        "MIOS_VENDOR_TOML": vendor, "MIOS_VENDOR_TOML_D": os.path.join(wcg__ROOT, "usr", "lib", "mios", "mios.d"),
        "MIOS_HOST_TOML": "/nonexistent/mios.toml", "MIOS_USER_TOML": user_toml or "/nonexistent/user/mios.toml"})

def wcg_vendor_edge():
    with open(wcg__VENDOR, "rb") as fh:
        return tomllib.load(fh)["theme"]["edge"]

class wcg_TestWmConfigGen(unittest.TestCase):
    """Test suite for Hyprland and Sway compositor config generation and hot-reloading."""

    def setUp(self):
        self._env = wcg_env()
        self._env.start()
        self.addCleanup(self._env.stop)

    def test_engine_init_and_palette(self):
        engine = wm_config_gen.WmConfigGenEngine(gaps_inner=6, gaps_outer=12, border_size=3, mock=True)
        self.assertEqual(engine.gaps_inner, 6)
        self.assertEqual(engine.gaps_outer, 12)
        self.assertEqual(engine.border_size, 3)
        self.assertIn("bg", engine.palette)
        self.assertIn("accent", engine.palette)
        self.assertIn("cursor", engine.palette)

    def test_generate_hyprland_conf(self):
        engine = wm_config_gen.WmConfigGenEngine(mock=True)
        conf = engine.generate_hyprland_conf()
        edge = wcg_vendor_edge()
        self.assertIn("monitor=,preferred,auto,1", conf)
        self.assertIn("exec-once = quickshell --config /usr/share/mios/quickshell/Config.qml", conf)
        self.assertNotIn("@@", conf)
        self.assertIn("general {", conf)
        self.assertIn(f"    gaps_in = {edge['wm_gaps_inner_px']}\n", conf)
        self.assertIn(f"    gaps_out = {edge['wm_gaps_outer_px']}\n", conf)
        self.assertIn(f"    border_size = {edge['wm_border_px']}\n", conf)
        self.assertIn("col.active_border =", conf)
        self.assertIn("decoration {", conf)
        self.assertIn("animations {", conf)

    def test_generate_sway_config(self):
        engine = wm_config_gen.WmConfigGenEngine(mock=True)
        conf = engine.generate_sway_config()
        self.assertIn("# MiOS Sway Configuration", conf)
        self.assertIn("set $mod Mod4", conf)
        self.assertIn(f"font pango:{engine.data['theme']['font']['family']} {engine.data['theme']['font']['size']}", conf)
        edge = wcg_vendor_edge()
        self.assertIn(f"gaps inner {edge['wm_gaps_inner_px']}\n", conf)
        self.assertIn(f"gaps outer {edge['wm_gaps_outer_px']}\n", conf)
        self.assertIn(f"default_border pixel {edge['wm_border_px']}\n", conf)
        self.assertIn("client.focused", conf)
        self.assertIn("bindsym $mod+Return exec alacritty", conf)

    def test_trigger_reload_mock(self):
        engine = wm_config_gen.WmConfigGenEngine(mock=True)
        res_h = engine.trigger_reload("hyprland")
        self.assertTrue(res_h["reloaded"])
        res_s = engine.trigger_reload("sway")
        self.assertTrue(res_s["reloaded"])

    def test_run_pipeline(self):
        engine = wm_config_gen.WmConfigGenEngine(mock=True)
        res = engine.run(wm="all", reload_active=True)
        self.assertEqual(res["status"], "success")
        self.assertGreater(res["hyprland_lines"], 20)
        self.assertGreater(res["sway_lines"], 20)
        self.assertIn("hyprland", res["reload"])
        self.assertIn("sway", res["reload"])
        self.assertTrue(res["mock"])

    def test_cli_hyprland_mock(self):
        test_args = ["wm_config_gen.py", "--wm", "hyprland", "--mock", "--json"]
        with patch.object(sys, "argv", test_args):
            exit_code = wm_config_gen.main()
            self.assertEqual(exit_code, 0)

    def test_cli_sway_mock(self):
        test_args = ["wm_config_gen.py", "--wm", "sway", "--mock", "--json"]
        with patch.object(sys, "argv", test_args):
            exit_code = wm_config_gen.main()
            self.assertEqual(exit_code, 0)

    def test_cli_all_reload_mock(self):
        test_args = ["wm_config_gen.py", "--wm", "all", "--reload", "--mock", "--json"]
        with patch.object(sys, "argv", test_args):
            exit_code = wm_config_gen.main()
            self.assertEqual(exit_code, 0)

    def test_user_tier_overrides_edge_geometry(self):
        with tempfile.TemporaryDirectory(prefix="mios-test-wcg-") as tmp:
            user = os.path.join(tmp, "mios.toml")
            with open(user, "w", encoding="utf-8") as fh:
                fh.write('[theme]\npadding = "3"\n[theme.edge]\nwm_gaps_outer_px = 7\n')
            with wcg_env(user_toml=user):
                engine = wm_config_gen.WmConfigGenEngine(mock=True)
                self.assertIn("    gaps_out = 7\n", engine.generate_hyprland_conf())
                self.assertIn("gaps outer 7\n", engine.generate_sway_config())
                self.assertEqual(engine.gaps_inner, wcg_vendor_edge()["wm_gaps_inner_px"])

    def test_missing_edge_key_names_it(self):
        with tempfile.TemporaryDirectory(prefix="mios-test-wcg-") as tmp:
            vendor = os.path.join(tmp, "mios.toml")
            with open(vendor, "w", encoding="utf-8") as fh:
                fh.write("[theme.edge]\nwm_gaps_inner_px = 0\nwm_border_px = 0\n")
            with wcg_env(vendor=vendor):
                with self.assertRaisesRegex(ValueError, r"\[theme\.edge\]\.wm_gaps_outer_px"):
                    wm_config_gen.WmConfigGenEngine(mock=True)
                self.assertEqual(wm_config_gen.WmConfigGenEngine(gaps_inner=1, gaps_outer=2, border_size=3, mock=True).gaps_outer, 2)

    def test_installed_goldens_both_sides(self):
        self.assertEqual(wm_config_gen.check_fixture(wcg__ROOT), 0)
        with tempfile.TemporaryDirectory(prefix="mios-test-wcg-") as tmp:
            self.assertEqual(wm_config_gen.write_fixture(tmp), 0)
            conf = os.path.join(tmp, "usr/share/mios/hyprland/hyprland.conf")
            os.chmod(conf, 0o640)
            with open(conf, encoding="utf-8") as fh:
                text = fh.read()
            with open(conf, "w", encoding="utf-8") as fh:
                fh.write(text.replace(f"    gaps_out = {wcg_vendor_edge()['wm_gaps_outer_px']}\n", "    gaps_out = 10\n"))
            with contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(wm_config_gen.check_fixture(tmp), 1)
            self.assertIn("hyprland.conf:", err.getvalue())
            self.assertIn("'    gaps_out = 10'", err.getvalue())
            self.assertNotIn("sway/config", err.getvalue())
            with patch.object(sys, "argv", ["wm_config_gen.py", "--write-fixture", tmp]):
                self.assertEqual(wm_config_gen.main(), 0)
            if os.name != "nt":
                self.assertEqual(os.stat(conf).st_mode & 0o777, 0o640)
            os.unlink(os.path.join(tmp, "usr/share/mios/sway/config"))
            with contextlib.redirect_stderr(io.StringIO()) as err2:
                self.assertEqual(wm_config_gen.check_fixture(tmp), 1)
            self.assertIn("sway/config: cannot read", err2.getvalue())

    def test_bake_renders_operator_edit(self):
        """65-bake-hyprland.sh renders from the build SSOT: an operator-tuned gap ships, not a build failure."""
        with open(os.path.join(wcg__ROOT, "automation", "65-bake-hyprland.sh"), encoding="utf-8") as fh:
            bake = fh.read()
        self.assertIn("for _gen in ux/wm_config_gen.py ", bake)
        self.assertIn('--write-fixture /\n', bake)
        self.assertNotIn("--check-fixture", bake)
        want = int(wcg_vendor_edge()["wm_gaps_outer_px"]) + 7
        with tempfile.TemporaryDirectory(prefix="mios-test-bake-") as tmp:
            with open(wcg__VENDOR, encoding="utf-8") as fh:
                text = fh.read()
            op = os.path.join(tmp, "op.toml")
            edited = re.sub(r"(?m)^(wm_gaps_outer_px\s*=\s*)\d+", rf"\g<1>{want}", text, count=1)
            self.assertNotEqual(edited, text)
            with open(op, "w", encoding="utf-8") as fh:
                fh.write(edited)
            with patch.dict(os.environ, {"MIOS_VENDOR_TOML": op}):
                self.assertEqual(wm_config_gen.write_fixture(os.path.join(tmp, "img")), 0)
                with open(os.path.join(tmp, "img/usr/share/mios/hyprland/hyprland.conf"), encoding="utf-8") as fh:
                    self.assertIn(f"    gaps_out = {want}\n", fh.read())
                with contextlib.redirect_stderr(io.StringIO()) as err:
                    self.assertEqual(wm_config_gen.check_fixture(wcg__ROOT), 1)
            self.assertIn("hyprland/hyprland.conf:", err.getvalue())

def wcg_main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(wcg_TestWmConfigGen)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1



def main() -> int:
    rc = 0 if unittest.main(argv=[sys.argv[0]], exit=False).result.wasSuccessful() else 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
