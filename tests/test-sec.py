#!/usr/bin/env python3
# AI-hint: Consolidated security test suite: auditd, boot chain, composefs, cosign, CVE/grype/SBOM/SLSA, FIDO2, LUKS, SELinux, SPIFFE, UKI, USBGuard, HITL approval, KASLR, lockdown, secrets.
"""Consolidated MiOS security tests (32 former tests/test-*.py sec suites folded into one module)."""
from __future__ import annotations


# ==== from tests/test-sec.py (prefix ar_) ====
"""Unit and integration test suite for AuditdRulesManager and CLI."""


import importlib.util
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ar__HERE = os.path.dirname(os.path.abspath(__file__))
ar__ROOT = os.path.normpath(os.path.join(ar__HERE, ".."))
ar__TARGET_PATH = os.path.join(ar__ROOT, "usr", "libexec", "mios", "sec", "auditd_rules.py")

ar_spec = importlib.util.spec_from_file_location("auditd_rules", ar__TARGET_PATH)
if ar_spec and ar_spec.loader:
    auditd_rules = importlib.util.module_from_spec(ar_spec)
    sys.modules[ar_spec.name] = auditd_rules
    ar_spec.loader.exec_module(auditd_rules)
else:
    raise ImportError(f"Could not load module from {ar__TARGET_PATH}")

class ar_TestAuditdRules(unittest.TestCase):
    """Test suite for Auditd rule rendering, syntax validation, deployment, and event logs."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="mios-test-auditd-")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_generate_rules_default_watches(self):
        manager = auditd_rules.AuditdRulesManager(mock=True)
        rules = manager.generate_rules()
        self.assertIn("-D", rules)
        self.assertIn("-b 8192", rules)
        self.assertTrue(any("-w /etc/mios/ -p wa -k mios_config_change" in r for r in rules))
        self.assertTrue(any("-w /usr/share/mios/ -p wa -k mios_config_change" in r for r in rules))

    def test_validate_rules_syntax_valid(self):
        manager = auditd_rules.AuditdRulesManager(mock=True)
        valid_rules = [
            "# Comment",
            "-D",
            "-b 8192",
            "-w /etc/mios/ -p wa -k mios_config_change",
            "-w /usr/share/mios/ -p rwx -k mios_audit",
        ]
        valid, errors = manager.validate_rules_syntax(valid_rules)
        self.assertTrue(valid)
        self.assertEqual(len(errors), 0)

    def test_validate_rules_syntax_invalid_flags(self):
        manager = auditd_rules.AuditdRulesManager(mock=True)
        invalid_rules = [
            "-w relative/path -p wa -k test",  # Non-absolute path
            "-w /etc/mios/ -p invalid_perms -k test",  # Invalid permissions
            "-z unknown_flag",  # Unrecognized flag
        ]
        valid, errors = manager.validate_rules_syntax(invalid_rules)
        self.assertFalse(valid)
        self.assertGreaterEqual(len(errors), 3)

    def test_deploy_rules_file_mock(self):
        manager = auditd_rules.AuditdRulesManager(mock=True)
        rules = manager.generate_rules()
        dest_file = os.path.join(self.temp_dir.name, "90-mios-config.rules")
        deployed = manager.deploy_rules_file(rules, destination=dest_file)
        self.assertTrue(deployed)
        self.assertTrue(os.path.exists(dest_file))

    def test_parse_audit_events_matching_key(self):
        manager = auditd_rules.AuditdRulesManager(mock=True)
        sample_log = (
            'type=SYSCALL msg=audit(1724670000.123:456): arch=c000003e syscall=2 success=yes '
            'exe="/usr/bin/touch" key="mios_config_change" comm="touch"\n'
            'type=PATH msg=audit(1724670000.123:456): item=0 name="/etc/mios/profile.toml" '
            'key="mios_config_change"\n'
            'type=SYSCALL msg=audit(1724670005.123:457): arch=c000003e syscall=2 success=yes '
            'exe="/usr/bin/ls" key="other_key" comm="ls"\n'
        )
        events = manager.parse_audit_events(sample_log, key_tag="mios_config_change")
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["key"], "mios_config_change")
        self.assertEqual(events[0]["exe"], "/usr/bin/touch")

    def test_cli_execution_generate_and_validate(self):
        test_args = [
            "auditd_rules.py",
            "--validate",
            "--mock",
            "--json",
        ]
        with patch.object(sys, "argv", test_args):
            exit_code = auditd_rules.main()
            self.assertEqual(exit_code, 0)

    def test_cli_execution_deploy(self):
        dest_file = os.path.join(self.temp_dir.name, "audit_test.rules")
        test_args = [
            "auditd_rules.py",
            "--deploy",
            "--rule-file", dest_file,
            "--mock",
            "--json",
        ]
        with patch.object(sys, "argv", test_args):
            exit_code = auditd_rules.main()
            self.assertEqual(exit_code, 0)

def ar_main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(ar_TestAuditdRules)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# ==== from tests/test-sec.py (prefix bcv_) ====
"""Automated tests for WS-SEC UKI PE headers, PCR 4/7/11 checks, and fs-verity digests."""


import importlib.util
import os
import sys
import unittest

bcv__HERE = os.path.dirname(os.path.abspath(__file__))
bcv__ROOT = os.path.normpath(os.path.join(bcv__HERE, ".."))
bcv__VERIFY_PATH = os.path.join(bcv__ROOT, "usr", "libexec", "mios", "sec", "verify-boot-chain.py")

bcv_spec = importlib.util.spec_from_file_location("verify_boot_chain", bcv__VERIFY_PATH)
if bcv_spec and bcv_spec.loader:
    verify_boot_chain = importlib.util.module_from_spec(bcv_spec)
    sys.modules[bcv_spec.name] = verify_boot_chain
    bcv_spec.loader.exec_module(verify_boot_chain)
else:
    raise ImportError(f"Could not load verify-boot-chain module from {bcv__VERIFY_PATH}")

class bcv_TestBootChainVerify(unittest.TestCase):
    """Validates UKI header structure, PCR 4/7/11 enforcement, and fs-verity mock digests."""

    def test_uki_mz_magic_validation(self):
        verifier = verify_boot_chain.BootChainVerifier(mock=True)
        valid_pe = b"MZ" + (b"\x00" * 62)
        invalid_pe = b"ELF" + (b"\x00" * 61)
        self.assertTrue(verifier.check_uki_structure(valid_pe))
        self.assertFalse(verifier.check_uki_structure(invalid_pe))

    def test_pcr_measurements_validation(self):
        verifier = verify_boot_chain.BootChainVerifier(mock=True)
        valid_pcrs = {
            4: "a" * 64,
            7: "b" * 64,
            11: "c" * 64,
        }
        self.assertTrue(verifier.verify_pcr_measurements(valid_pcrs))

        missing_pcr11 = {
            4: "a" * 64,
            7: "b" * 64,
        }
        self.assertFalse(verifier.verify_pcr_measurements(missing_pcr11))

        invalid_len = {
            4: "short",
            7: "b" * 64,
            11: "c" * 64,
        }
        self.assertFalse(verifier.verify_pcr_measurements(invalid_len))

    def test_cli_mock_and_json_verification(self):
        rc = verify_boot_chain.run_verification(mock=True, json_output=False)
        self.assertEqual(rc, 0)
        rc_json = verify_boot_chain.run_verification(mock=True, json_output=True)
        self.assertEqual(rc_json, 0)

def bcv_main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(bcv_TestBootChainVerify)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# ==== from tests/test-sec.py (prefix cfv_) ====
"""Unit and integration test suite for ComposefsVerifier and CLI."""


import importlib.util
import json
import os
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

cfv__HERE = os.path.dirname(os.path.abspath(__file__))
cfv__ROOT = os.path.normpath(os.path.join(cfv__HERE, ".."))
cfv__TARGET_PATH = os.path.join(cfv__ROOT, "usr", "libexec", "mios", "sec", "composefs_verify.py")

cfv_spec = importlib.util.spec_from_file_location("composefs_verify", cfv__TARGET_PATH)
if cfv_spec and cfv_spec.loader:
    composefs_verify = importlib.util.module_from_spec(cfv_spec)
    sys.modules[cfv_spec.name] = composefs_verify
    cfv_spec.loader.exec_module(composefs_verify)
else:
    raise ImportError(f"Could not load module from {cfv__TARGET_PATH}")

class cfv_TestComposefsVerify(unittest.TestCase):
    """Test suite for Composefs headers, fs-verity Merkle trees, and prepare-root configuration."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="mios-test-cfs-")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_parse_header_valid_magic_bytes(self):
        verifier = composefs_verify.ComposefsVerifier(mock=True)
        # Valid header with LE magic
        valid_hdr = struct.pack("<IHHQQ", composefs_verify.COMPOSEFS_MAGIC_LE, 1, 0, 4096, 1048576) + (b"\x00" * 36)
        res = verifier.parse_header(valid_hdr)
        self.assertTrue(res["valid"])
        self.assertEqual(res["version"], 1)

    def test_parse_header_alt_magic_bytes(self):
        verifier = composefs_verify.ComposefsVerifier(mock=True)
        alt_hdr = b"cfs\x00" + struct.pack("<HHQQ", 1, 0, 4096, 1048576) + (b"\x00" * 36)
        res = verifier.parse_header(alt_hdr)
        self.assertTrue(res["valid"])

    def test_parse_header_invalid_magic_returns_invalid(self):
        verifier = composefs_verify.ComposefsVerifier(mock=True)
        bad_hdr = b"BADMAGIC12345678" + (b"\x00" * 48)
        res = verifier.parse_header(bad_hdr)
        self.assertFalse(res["valid"])
        self.assertIn("Invalid magic", res["error"])

    def test_parse_header_too_short_returns_invalid(self):
        verifier = composefs_verify.ComposefsVerifier(mock=True)
        short_hdr = b"SHORT"
        res = verifier.parse_header(short_hdr)
        self.assertFalse(res["valid"])
        self.assertIn("Header too short", res["error"])

    def test_compute_fsverity_digest_mock_and_real_file(self):
        verifier = composefs_verify.ComposefsVerifier(mock=True)
        # Mock path
        digest = verifier.compute_fsverity_digest("/ostree/deploy/mock.img")
        self.assertEqual(len(digest), 64)

        # Real file
        real_img = os.path.join(self.temp_dir.name, "test.img")
        with open(real_img, "wb") as f:
            f.write(b"\x00" * 8192)

        v_real = composefs_verify.ComposefsVerifier(mock=False)
        real_digest = v_real.compute_fsverity_digest(real_img)
        self.assertEqual(len(real_digest), 64)

    def test_check_prepare_root_config_mock(self):
        verifier = composefs_verify.ComposefsVerifier(mock=True)
        # A path that cannot exist: an ostree host (machine-os CI) has a real
        # prepare-root.conf at the default path, which mock mode rightly reads.
        res = verifier.check_prepare_root_config(os.path.join(self.temp_dir.name, "absent.conf"))
        self.assertTrue(res["composefs_enabled"])
        self.assertEqual(res["composefs_mode"], "verity")
        self.assertTrue(res["strict_integrity"])

    def test_check_prepare_root_config_real_file(self):
        conf_path = os.path.join(self.temp_dir.name, "prepare-root.conf")
        with open(conf_path, "w", encoding="utf-8") as f:
            f.write("[composefs]\nenabled = verity\n")

        v_real = composefs_verify.ComposefsVerifier(mock=False)
        res = v_real.check_prepare_root_config(conf_path)
        self.assertTrue(res["composefs_enabled"])
        self.assertEqual(res["composefs_mode"], "verity")

    def test_verify_rootfs_integrity_mock(self):
        verifier = composefs_verify.ComposefsVerifier(mock=True)
        res = verifier.verify_rootfs_integrity(
            image_path="/ostree/mock.img", conf_path=os.path.join(self.temp_dir.name, "absent.conf"))
        self.assertEqual(res["status"], "pass")
        self.assertTrue(res["header_valid"])
        self.assertTrue(res["signature_valid"])
        self.assertEqual(res["composefs_mode"], "verity")

    def test_cli_execution_header_check(self):
        test_args = [
            "composefs_verify.py",
            "--header-check",
            "--mock",
            "--json",
        ]
        with patch.object(sys, "argv", test_args):
            exit_code = composefs_verify.main()
            self.assertEqual(exit_code, 0)

    def test_cli_execution_compute_digest(self):
        test_args = [
            "composefs_verify.py",
            "--compute-digest",
            "--mock",
            "--json",
        ]
        with patch.object(sys, "argv", test_args):
            exit_code = composefs_verify.main()
            self.assertEqual(exit_code, 0)

    def test_cli_execution_check_config(self):
        test_args = [
            "composefs_verify.py",
            "--check-config",
            "--mock",
            "--json",
        ]
        with patch.object(sys, "argv", test_args):
            exit_code = composefs_verify.main()
            self.assertEqual(exit_code, 0)

def cfv_main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(cfv_TestComposefsVerify)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# ==== from tests/test-sec.py (prefix cc_) ====
"""Unit and integration test suite for KVCompactEngine and mios_kv_compact CLI (task_492)."""


import importlib.util
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

cc__HERE = os.path.dirname(os.path.abspath(__file__))
cc__ROOT = os.path.normpath(os.path.join(cc__HERE, ".."))
cc__TARGET_PATH = os.path.join(cc__ROOT, "usr", "lib", "mios", "agent-pipe", "mios_kv_compact.py")

cc_spec = importlib.util.spec_from_file_location("mios_kv_compact", cc__TARGET_PATH)
if cc_spec and cc_spec.loader:
    mios_kv_compact = importlib.util.module_from_spec(cc_spec)
    sys.modules[cc_spec.name] = mios_kv_compact
    cc_spec.loader.exec_module(mios_kv_compact)
else:
    raise ImportError(f"Could not load module from {cc__TARGET_PATH}")

class cc_TestContextCompact(unittest.TestCase):
    """Test suite for context compaction thresholds, system prompt immutability, and episodic archives."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory(prefix="mios-test-compact-")
        self.episode_dir = os.path.join(self.tmpdir.name, "episodes")
        os.makedirs(self.episode_dir, exist_ok=True)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_estimate_tokens(self):
        msg = {"role": "user", "content": "Hello MiOS agent pipe"}
        tokens = mios_kv_compact.estimate_tokens(msg)
        self.assertGreater(tokens, 0)

        msgs = [
            {"role": "system", "content": "System identity"},
            {"role": "user", "content": "User request"},
            {"role": "assistant", "content": "Assistant answer"},
        ]
        total_tokens = mios_kv_compact.estimate_tokens(msgs)
        self.assertGreater(total_tokens, tokens)

    def test_extract_factual_anchors(self):
        text = "Modified file usr/libexec/mios/sec/livepatch_mgr.py and completed task_545; see CVE-2026-1001."
        anchors = mios_kv_compact.extract_factual_anchors(text)
        self.assertTrue(any("livepatch_mgr.py" in a for a in anchors))
        self.assertIn("task_545", anchors)
        self.assertIn("CVE-2026-1001", anchors)

    def test_should_compact_threshold(self):
        engine = mios_kv_compact.KVCompactEngine(max_tokens=1000, compact_threshold=0.75, mock=True)
        small_msgs = [{"role": "user", "content": "Small prompt"}]
        self.assertFalse(engine.should_compact(small_msgs))

        # Long conversation exceeding 750 tokens (~3000 chars)
        large_msgs = [
            {"role": "system", "content": "System prompt"},
            {"role": "assistant", "content": "Very long output text with logs and tool execution " * 100},
        ]
        self.assertTrue(engine.should_compact(large_msgs))

    def test_system_prompt_immutability(self):
        engine = mios_kv_compact.KVCompactEngine(episode_dir=self.episode_dir, mock=True)
        canonical_system_prompt = "You are MiOS Master AI under Architectural Law 5."
        messages = [
            {"role": "system", "content": canonical_system_prompt},
            {"role": "user", "content": "Query 1"},
            {"role": "assistant", "content": "Verbose tool trace " * 60},
            {"role": "tool", "name": "view_file", "content": "Long file dump line " * 80},
            {"role": "user", "content": "Latest query"},
        ]

        res = engine.compact_messages(messages, force=True)
        self.assertTrue(res["compacted"])

        compacted_messages = res["messages"]
        self.assertEqual(compacted_messages[0]["role"], "system")
        self.assertEqual(compacted_messages[0]["content"], canonical_system_prompt)

    def test_token_reduction_and_milestone_creation(self):
        engine = mios_kv_compact.KVCompactEngine(episode_dir=self.episode_dir, mock=True)
        messages = [
            {"role": "system", "content": "System instructions."},
            {"role": "user", "content": "Step 1: Check hardware status."},
            {"role": "assistant", "content": "Running lspci and dmesg tools... " * 50},
            {"role": "tool", "name": "bash", "content": "00:02.0 VGA compatible controller Intel Corporation... " * 100},
            {"role": "assistant", "content": "Step 2: Allocate model matrix... " * 50},
            {"role": "tool", "name": "bash", "content": "Allocated 8GB VRAM for Qwen2.5-Coder... " * 100},
            {"role": "user", "content": "Step 3: What is the final allocation?"},
            {"role": "assistant", "content": "Ready for final confirmation."},
        ]

        res = engine.compact_messages(messages, preserve_recent_turns=2, force=True)
        self.assertTrue(res["compacted"])
        self.assertLess(res["final_tokens"], res["initial_tokens"])
        self.assertLess(res["reduction_ratio"], 0.60)

        compacted_msgs = res["messages"]
        self.assertTrue(any(m.get("_compacted_milestone") for m in compacted_msgs))
        self.assertEqual(compacted_msgs[-1]["content"], "Ready for final confirmation.")
        self.assertEqual(compacted_msgs[-2]["content"], "Step 3: What is the final allocation?")

    def test_episodic_archive_persistence(self):
        engine = mios_kv_compact.KVCompactEngine(episode_dir=self.episode_dir, mock=False)
        messages = [
            {"role": "system", "content": "System instructions"},
            {"role": "user", "content": "Do task"},
            {"role": "assistant", "content": "Heavy processing " * 50},
            {"role": "user", "content": "Final step"},
        ]

        res = engine.compact_messages(messages, session_id="test-session-123", force=True)
        self.assertTrue(res["compacted"])
        self.assertTrue(res["archive"]["archived"])
        self.assertTrue(os.path.isfile(res["archive"]["path"]))

        with open(res["archive"]["path"], "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["session_id"], "test-session-123")
        self.assertEqual(data["turns_count"], 4)

    def test_multi_turn_60_turns_compaction(self):
        engine = mios_kv_compact.KVCompactEngine(episode_dir=self.episode_dir, mock=True)
        messages = [{"role": "system", "content": "System prompt"}]
        for i in range(30):
            messages.append({"role": "user", "content": f"Turn {i} question regarding file_{i}.py"})
            messages.append({"role": "assistant", "content": f"Turn {i} verbose response log " * 20})

        self.assertEqual(len(messages), 61)
        res = engine.compact_messages(messages, force=True, preserve_recent_turns=4)
        self.assertTrue(res["compacted"])
        # Head (1) + Milestone (1) + Tail (4) = 6 messages
        self.assertEqual(len(res["messages"]), 6)
        self.assertEqual(res["messages"][0]["role"], "system")
        self.assertTrue(res["messages"][1].get("_compacted_milestone"))

    def test_cli_execution_mock(self):
        with patch.object(sys, "argv", ["mios_kv_compact.py", "--mock", "--compact", "--json"]):
            code = mios_kv_compact.main()
            self.assertEqual(code, 0)


# ==== from tests/test-sec.py (prefix cosv_) ====
"""Unit and integration test suite for CosignVerifier and CLI."""


import importlib.util
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

cosv__HERE = os.path.dirname(os.path.abspath(__file__))
cosv__ROOT = os.path.normpath(os.path.join(cosv__HERE, ".."))
cosv__TARGET_PATH = os.path.join(cosv__ROOT, "usr", "libexec", "mios", "sec", "cosign_verify.py")

cosv_spec = importlib.util.spec_from_file_location("cosign_verify", cosv__TARGET_PATH)
if cosv_spec and cosv_spec.loader:
    cosign_verify = importlib.util.module_from_spec(cosv_spec)
    sys.modules[cosv_spec.name] = cosign_verify
    cosv_spec.loader.exec_module(cosign_verify)
else:
    raise ImportError(f"Could not load module from {cosv__TARGET_PATH}")

class cosv_TestCosignVerify(unittest.TestCase):
    """Test suite for Cosign OCI container image signatures, Rekor proofs, and policy.json rules."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="mios-test-cosign-")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_verify_image_signature_valid_image(self):
        verifier = cosign_verify.CosignVerifier(mock=True)
        res = verifier.verify_image_signature("ghcr.io/mios-dev/mios:latest")
        self.assertTrue(res["valid"])
        self.assertEqual(res["image_ref"], "ghcr.io/mios-dev/mios:latest")
        self.assertIn("sha256:", res["digest"])

    def test_verify_image_signature_unsigned_tampered_rejected(self):
        verifier = cosign_verify.CosignVerifier(mock=True)
        res_unsigned = verifier.verify_image_signature("ghcr.io/untrusted/unsigned-image:latest")
        self.assertFalse(res_unsigned["valid"])

        res_tampered = verifier.verify_image_signature("ghcr.io/mios-dev/tampered-payload:v1")
        self.assertFalse(res_tampered["valid"])

    def test_verify_rekor_inclusion_bundle_dict(self):
        verifier = cosign_verify.CosignVerifier(mock=True)
        bundle = {
            "Payload": {
                "logIndex": 987654,
                "integratedTime": 1724670000,
            },
            "rekor_verified": True,
        }
        self.assertTrue(verifier.verify_rekor_inclusion(bundle))

    def test_audit_policy_json_mock_strict(self):
        verifier = cosign_verify.CosignVerifier(mock=True)
        res = verifier.audit_policy_json("/etc/containers/policy.json")
        self.assertTrue(res["policy_strict"])
        self.assertEqual(res["insecure_rules_detected"], 0)

    def test_audit_policy_json_detects_insecure_accept_anything(self):
        insecure_policy = {
            "default": [{"type": "insecureAcceptAnything"}],
            "transports": {
                "docker": {
                    "ghcr.io/untrusted": [{"type": "insecureAcceptAnything"}]
                }
            }
        }
        pol_path = os.path.join(self.temp_dir.name, "insecure_policy.json")
        with open(pol_path, "w", encoding="utf-8") as f:
            json.dump(insecure_policy, f)

        v_real = cosign_verify.CosignVerifier(mock=False)
        res = v_real.audit_policy_json(pol_path)
        self.assertFalse(res["policy_strict"])
        self.assertEqual(res["insecure_rules_detected"], 2)
        self.assertIn("default", res["insecure_scopes"])
        self.assertIn("docker:ghcr.io/untrusted", res["insecure_scopes"])

    def test_evaluate_upgrade_safety_pass_and_fail(self):
        verifier = cosign_verify.CosignVerifier(mock=True)
        res_pass = verifier.evaluate_upgrade_safety("ghcr.io/mios-dev/mios:latest")
        self.assertEqual(res_pass["status"], "pass")
        self.assertTrue(res_pass["signature_valid"])
        self.assertTrue(res_pass["rekor_verified"])

        res_fail = verifier.evaluate_upgrade_safety("ghcr.io/malicious/rootkit:latest")
        self.assertEqual(res_fail["status"], "fail")

    def test_cli_execution_verify_signature(self):
        test_args = [
            "cosign_verify.py",
            "--verify-signature",
            "--image", "ghcr.io/mios-dev/mios:latest",
            "--mock",
            "--json",
        ]
        with patch.object(sys, "argv", test_args):
            exit_code = cosign_verify.main()
            self.assertEqual(exit_code, 0)

    def test_cli_execution_audit_policy(self):
        test_args = [
            "cosign_verify.py",
            "--audit-policy",
            "--mock",
            "--json",
        ]
        with patch.object(sys, "argv", test_args):
            exit_code = cosign_verify.main()
            self.assertEqual(exit_code, 0)

    def test_cli_execution_evaluate_upgrade(self):
        test_args = [
            "cosign_verify.py",
            "--evaluate-upgrade",
            "--mock",
            "--json",
        ]
        with patch.object(sys, "argv", test_args):
            exit_code = cosign_verify.main()
            self.assertEqual(exit_code, 0)

def cosv_main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(cosv_TestCosignVerify)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# ==== from tests/test-sec.py (prefix cve_) ====
"""Automated unit test suite for MiOS CVE Vulnerability Scanner."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "usr", "libexec", "mios", "sec"))

from cve_scan import OCIImageVulnerabilityScanner, Vulnerability

class cve_TestCVEScanner(unittest.TestCase):
    def setUp(self):
        self.scanner = OCIImageVulnerabilityScanner(dry_run=True)

    def test_clean_image_passes_audit(self):
        """Test clean baseline image with zero critical CVEs passes gate."""
        report = self.scanner.scan_image("localhost/mios:latest", mock_vulns=[])
        self.assertTrue(report["passed"])
        self.assertEqual(report["summary"]["critical"], 0)

    def test_critical_cve_blocks_image_publication(self):
        """Test injecting a CVSS 9.8 Critical vulnerability fails the gate."""
        mock_cves = [
            Vulnerability(
                cve_id="CVE-2026-9999",
                package="openssl-libs",
                severity="CRITICAL",
                cvss_score=9.8,
                fix_version="3.2.1-2.fc41",
                description="Remote code execution in certificate verification",
            )
        ]
        report = self.scanner.scan_image("localhost/mios:latest", mock_vulns=mock_cves)
        self.assertFalse(report["passed"])
        self.assertEqual(report["summary"]["critical"], 1)


# ==== from tests/test-sec.py (prefix ec1_) ====
import os
import sys
import unittest
import tempfile
import stat
import secrets
import json
import tomllib
import subprocess
import time
import math
import importlib.util
import importlib.machinery

ec1__HERE = os.path.abspath(os.path.dirname(__file__)) if "__file__" in globals() else os.path.abspath(".")
ec1__ROOT = os.path.normpath(os.path.join(ec1__HERE, "..")) if os.path.basename(ec1__HERE) == "tests" else ec1__HERE

def ec1_load_module(name, rel_path):
    full_path = os.path.join(ec1__ROOT, rel_path)
    spec = importlib.util.spec_from_file_location(name, full_path)
    if spec and spec.loader:
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        return mod
    loader = importlib.machinery.SourceFileLoader(name, full_path)
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

sys.path.insert(0, os.path.join(ec1__ROOT, "usr", "lib", "mios"))
sys.path.insert(0, os.path.join(ec1__ROOT, "lib", "mios"))

ec1_wasm = ec1_load_module("wasm_sandbox", "usr/libexec/mios/node/wasm_sandbox.py")
ec1_mat = ec1_load_module("materialize_config_toml", "usr/libexec/mios/materialize-config-toml.py")
ec1_cephfs = ec1_load_module("cephfs_provision", "usr/libexec/mios/mios-cephfs-provision")
ec1_boot = ec1_load_module("verify_boot_chain", "usr/libexec/mios/sec/verify-boot-chain.py")
ec1_sec = ec1_load_module("rotate_quadlet_secrets", "usr/libexec/mios/sec/rotate-quadlet-secrets.py")
ec1_lg = ec1_load_module("setup_looking_glass", "usr/libexec/mios/vfio/setup-looking-glass.py")

class ec1_TestAdversarialWasmSandbox(unittest.TestCase):
    """Stress tests on Wasm Sandbox fuel, memory, opcodes, and host imports."""

    def test_fuel_exhaustion_boundaries(self):
        # 1. Exact fuel limit boundary
        config = ec1_wasm.WasmExecutionConfig(max_fuel=1000)
        engine = ec1_wasm.WasmSandboxEngine(config)

        # fuel == max_fuel (1000 == 1000) -> should PASS
        res_exact = engine.execute(b"\x00asm\x01\x00\x00\x00", b"test", simulated_fuel_cost=1000)
        self.assertTrue(res_exact.success)
        self.assertEqual(res_exact.exit_code, 0)
        self.assertEqual(res_exact.fuel_consumed, 1000)

        # fuel == max_fuel + 1 (1001 > 1000) -> should FAIL with 124
        res_over = engine.execute(b"\x00asm\x01\x00\x00\x00", b"test", simulated_fuel_cost=1001)
        self.assertFalse(res_over.success)
        self.assertEqual(res_over.exit_code, 124)
        self.assertIn("Fuel limit exhausted", res_over.error_msg)

        # zero fuel limit with non-zero cost -> should FAIL with 124
        config_zero = ec1_wasm.WasmExecutionConfig(max_fuel=0)
        engine_zero = ec1_wasm.WasmSandboxEngine(config_zero)
        res_zero = engine_zero.execute(b"\x00asm\x01\x00\x00\x00", b"test", simulated_fuel_cost=1)
        self.assertFalse(res_zero.success)
        self.assertEqual(res_zero.exit_code, 124)

    def test_memory_ceiling_boundaries(self):
        limit = 64 * 1024 * 1024  # 64MB
        config = ec1_wasm.WasmExecutionConfig(max_memory_bytes=limit)
        engine = ec1_wasm.WasmSandboxEngine(config)

        # Exact boundary: alloc == limit -> PASS
        res_exact = engine.execute(b"\x00asm\x01\x00\x00\x00", b"test", simulated_alloc_bytes=limit)
        self.assertTrue(res_exact.success)
        self.assertEqual(res_exact.exit_code, 0)
        self.assertEqual(res_exact.memory_used_bytes, limit)

        # Over-allocation by 1 byte: alloc == limit + 1 -> FAIL with 137
        res_over_1 = engine.execute(b"\x00asm\x01\x00\x00\x00", b"test", simulated_alloc_bytes=limit + 1)
        self.assertFalse(res_over_1.success)
        self.assertEqual(res_over_1.exit_code, 137)
        self.assertIn("Memory limit exceeded", res_over_1.error_msg)

        # Massive over-allocation: 1GB, 10GB
        res_huge = engine.execute(b"\x00asm\x01\x00\x00\x00", b"test", simulated_alloc_bytes=10 * 1024 * 1024 * 1024)
        self.assertFalse(res_huge.success)
        self.assertEqual(res_huge.exit_code, 137)

    def test_host_imports_stress_and_edge_cases(self):
        # Test HostImports with empty payload
        host_empty = ec1_wasm.HostImports(b"")
        self.assertEqual(host_empty.mios_sys_read(0, 10), b"")
        self.assertEqual(host_empty.mios_sys_read(100, 50), b"")

        # Test HostImports with large payload and out-of-bounds offsets
        payload = b"X" * 1_000_000  # 1MB input
        host = ec1_wasm.HostImports(payload)
        self.assertEqual(len(host.mios_sys_read(0, 500)), 500)
        self.assertEqual(len(host.mios_sys_read(999_900, 200)), 100) # clamped to remaining bytes
        self.assertEqual(host.mios_sys_read(2_000_000, 100), b"")

        # Test mios_sys_write with binary data including null bytes and unicode
        written = host.mios_sys_write(b"\x00\xff\xfe\xfd" * 1000)
        self.assertEqual(written, 4000)
        self.assertEqual(len(host.output_data), 4000)

        # Test logging special characters and rapid succession
        for i in range(100):
            host.mios_sys_log(f"log_{i}: \x00 <xml> & \" ' \n")
        self.assertEqual(len(host.logs), 100)
        self.assertTrue(host.logs[0].startswith("[wasm_guest] log_0:"))

        # Test time monotonically increases or stays non-negative
        t1 = host.mios_sys_time()
        t2 = host.mios_sys_time()
        self.assertIsInstance(t1, int)
        self.assertGreater(t1, 0)
        self.assertGreaterEqual(t2, t1)

        # Test exit codes
        host.mios_sys_exit(42)
        self.assertTrue(host.exited)
        self.assertEqual(host.exit_code, 42)

class ec1_TestAdversarialTOMLMaterializer(unittest.TestCase):
    """Stress tests on TOML key escaping and value formatting against the TOML spec."""

    def test_escape_toml_key_special_characters(self):
        # Bare keys: letters, digits, underscores, hyphens
        self.assertEqual(ec1_mat.escape_toml_key("valid_key-123"), "valid_key-123")
        self.assertEqual(ec1_mat.escape_toml_key("ALPHA_BETA"), "ALPHA_BETA")

        # Special characters must be quoted
        self.assertEqual(ec1_mat.escape_toml_key("key.with.dots"), '"key.with.dots"')
        self.assertEqual(ec1_mat.escape_toml_key("key with spaces"), '"key with spaces"')
        self.assertEqual(ec1_mat.escape_toml_key("key:colon"), '"key:colon"')
        self.assertEqual(ec1_mat.escape_toml_key("key/slash"), '"key/slash"')
        self.assertEqual(ec1_mat.escape_toml_key('key"with"quotes'), '"key\\"with\\"quotes"')
        self.assertEqual(ec1_mat.escape_toml_key("key\\backslash"), '"key\\\\backslash"')

        # Check that generated escaped keys parse in a TOML doc
        test_keys = ["foo", "foo-bar", "foo_bar", "foo.bar", "foo bar", "foo:bar", 'foo"bar', "foo\\bar", "123", ""]
        for k in test_keys:
            esc = ec1_mat.escape_toml_key(k)
            doc = f"{esc} = 42\n"
            try:
                parsed = tomllib.loads(doc)
                self.assertIn(k, parsed)
                self.assertEqual(parsed[k], 42)
            except Exception as e:
                self.fail(f"Failed to parse TOML key {k!r} (escaped as {esc!r}): {e}")

    def test_format_toml_value_escaped_strings_and_types(self):
        # Standard primitives
        self.assertEqual(ec1_mat.format_toml_value(True), "true")
        self.assertEqual(ec1_mat.format_toml_value(False), "false")
        self.assertEqual(ec1_mat.format_toml_value(0), "0")
        self.assertEqual(ec1_mat.format_toml_value(-42), "-42")
        self.assertEqual(ec1_mat.format_toml_value(3.14159), "3.14159")

        # Strings with escaped characters
        s_quote = 'string with "quotes" and \\backslashes\\ and \nnewlines'
        formatted_s = ec1_mat.format_toml_value(s_quote)
        doc = f"val = {formatted_s}\n"
        parsed = tomllib.loads(doc)
        self.assertEqual(parsed["val"], s_quote)

        # Unicode and emojis
        unicode_str = "MiOS 🚀 日本語 🤖 \u2764"
        formatted_u = ec1_mat.format_toml_value(unicode_str)
        doc_u = f"val = {formatted_u}\n"
        parsed_u = tomllib.loads(doc_u)
        self.assertEqual(parsed_u["val"], unicode_str)

    def test_format_toml_value_nested_tables_and_lists(self):
        # Nested list of strings and ints
        nested_list = [1, "two", [3, "four", [5, 6]]]
        fmt_list = ec1_mat.format_toml_value(nested_list)
        doc_list = f"list_key = {fmt_list}\n"
        parsed_list = tomllib.loads(doc_list)
        self.assertEqual(parsed_list["list_key"], nested_list)

        # Nested dict (inline table)
        nested_dict = {
            "a": 1,
            "b": "text",
            "c": {"sub1": True, "sub2": [10, 20]}
        }
        fmt_dict = ec1_mat.format_toml_value(nested_dict)
        doc_dict = f"table_key = {fmt_dict}\n"
        parsed_dict = tomllib.loads(doc_dict)
        self.assertEqual(parsed_dict["table_key"], nested_dict)

        # Empty structures
        self.assertEqual(ec1_mat.format_toml_value([]), "[]")
        self.assertEqual(ec1_mat.format_toml_value({}), "{}")
        parsed_empty = tomllib.loads("empty_list = []\nempty_dict = {}\n")
        self.assertEqual(parsed_empty["empty_list"], [])
        self.assertEqual(parsed_empty["empty_dict"], {})

    def test_format_toml_value_null_observation(self):
        # Document the behavior for None
        res_none = ec1_mat.format_toml_value(None)
        self.assertEqual(res_none, "None")
        # In TOML, 'None' as an unquoted token is invalid syntax
        with self.assertRaises(Exception):
            tomllib.loads(f"key = {res_none}\n")

class ec1_TestAdversarialCephFSProvisioner(unittest.TestCase):
    """Stress tests on CephFS Provisioner UID resolution, user lookup, and argument handling."""

    def test_resolve_uid_numeric_and_edge_cases(self):
        self.assertEqual(ec1_cephfs.resolve_uid_number("1000"), 1000)
        self.assertEqual(ec1_cephfs.resolve_uid_number("0"), 0)
        self.assertEqual(ec1_cephfs.resolve_uid_number("  65534  "), 65534)

        # Non-existent user fallback
        self.assertEqual(ec1_cephfs.resolve_uid_number("nonexistent_user_xyz_9999"), 1000)
        self.assertEqual(ec1_cephfs.resolve_uid_number(""), 1000)
        self.assertEqual(ec1_cephfs.resolve_uid_number("!@#$%^"), 1000)

    def test_get_user_info_numeric_and_edge_cases(self):
        # Numeric UID string
        name, gid = ec1_cephfs.get_user_info("1000")
        self.assertTrue(len(name) > 0)
        self.assertIsInstance(gid, int)

        # String username
        name_str, gid_str = ec1_cephfs.get_user_info("root")
        self.assertEqual(name_str, "root")
        self.assertIsInstance(gid_str, int)

        # Arbitrary/Unknown user string fallback
        name_unk, gid_unk = ec1_cephfs.get_user_info("unknown_test_user")
        self.assertEqual(name_unk, "unknown_test_user")
        self.assertEqual(gid_unk, 1000)

    def test_load_cephfs_config_schema(self):
        cfg = ec1_cephfs.load_cephfs_config()
        self.assertIsInstance(cfg, dict)
        self.assertIn("cluster_name", cfg)
        self.assertIn("monitors", cfg)
        self.assertIn("fs_name", cfg)
        self.assertIn("tenant_id", cfg)
        self.assertIn("keyring_dir", cfg)
        self.assertIn("subvolume_mode", cfg)

    def test_cli_argument_handling(self):
        # Test subcommands with missing arguments via subprocess
        script_path = os.path.join(ec1__ROOT, "usr", "libexec", "mios", "mios-cephfs-provision")

        # When CephFS is disabled, main() exits 0 as no-op early
        p = subprocess.run([sys.executable, script_path], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0)
        self.assertIn("CephFS storage integration is disabled", p.stdout)

class ec1_TestAdversarialBootChainVerifier(unittest.TestCase):
    """Stress tests on UKI PE header magic, PCR measurements, and fs-verity digests."""

    def test_check_uki_structure_pe_magic(self):
        verifier = ec1_boot.BootChainVerifier(mock=True)

        # Valid MZ PE magic
        valid_pe = b"MZ" + (b"\x00" * 62)
        self.assertTrue(verifier.check_uki_structure(valid_pe))

        # Valid with trailing payload
        self.assertTrue(verifier.check_uki_structure(b"MZ" + (b"\xff" * 2048)))

        # Invalid magic: reversed "ZM"
        self.assertFalse(verifier.check_uki_structure(b"ZM" + (b"\x00" * 62)))

        # Invalid magic: ELF header
        self.assertFalse(verifier.check_uki_structure(b"\x7fELF" + (b"\x00" * 60)))

        # Truncated headers (< 64 bytes)
        self.assertFalse(verifier.check_uki_structure(b""))
        self.assertFalse(verifier.check_uki_structure(b"MZ"))
        self.assertFalse(verifier.check_uki_structure(b"MZ" + (b"\x00" * 61)))  # 63 bytes

    def test_verify_pcr_measurements(self):
        verifier = ec1_boot.BootChainVerifier(mock=True)

        valid_pcrs = {
            4: "4" * 64,
            7: "7" * 64,
            11: "a" * 64,
        }
        self.assertTrue(verifier.verify_pcr_measurements(valid_pcrs))

        # Missing required PCRs
        for missing in [4, 7, 11]:
            corrupted = dict(valid_pcrs)
            del corrupted[missing]
            self.assertFalse(verifier.verify_pcr_measurements(corrupted))

        # Corrupted PCR lengths (SHA256 must be 64 hex chars)
        self.assertFalse(verifier.verify_pcr_measurements({4: "a" * 63, 7: "b" * 64, 11: "c" * 64}))
        self.assertFalse(verifier.verify_pcr_measurements({4: "a" * 65, 7: "b" * 64, 11: "c" * 64}))
        self.assertFalse(verifier.verify_pcr_measurements({4: "", 7: "b" * 64, 11: "c" * 64}))

        # Extra PCRs present alongside required 4, 7, 11
        extended_pcrs = dict(valid_pcrs)
        extended_pcrs[0] = "0" * 64
        extended_pcrs[1] = "1" * 64
        self.assertTrue(verifier.verify_pcr_measurements(extended_pcrs))

    def test_verify_fsverity_digest_real_file(self):
        verifier = ec1_boot.BootChainVerifier(mock=False)
        with tempfile.NamedTemporaryFile(delete=False) as tf:
            tf.write(b"fsverity_test_content_block")
            temp_name = tf.name

        try:
            import hashlib
            expected = hashlib.sha256(b"fsverity_test_content_block").hexdigest()
            # Matching digest
            self.assertTrue(verifier.verify_fsverity_digest(temp_name, expected))
            self.assertTrue(verifier.verify_fsverity_digest(temp_name, expected.upper()))

            # Mismatched digest
            wrong = hashlib.sha256(b"corrupted_block").hexdigest()
            self.assertFalse(verifier.verify_fsverity_digest(temp_name, wrong))
        finally:
            os.remove(temp_name)

    def test_cli_verification_runner(self):
        rc_mock = ec1_boot.run_verification(mock=True, json_output=True)
        self.assertEqual(rc_mock, 0)

class ec1_TestAdversarialQuadletSecrets(unittest.TestCase):
    """Stress tests on Quadlet secrets token entropy, idempotency, and permission hardening."""

    def test_token_rotation_entropy_and_uniqueness(self):
        hardener = ec1_sec.QuadletSecretsHardener()
        tokens = set()
        count = 10_000

        for _ in range(count):
            tok, line = hardener.generate_rotated_secret("TEST_SECRET", length_bytes=32)
            self.assertEqual(len(tok), 64)
            self.assertTrue(all(c in "0123456789abcdef" for c in tok))
            self.assertEqual(line, f"TEST_SECRET={tok}\n")
            tokens.add(tok)

        # Verify zero collisions across 10,000 generated 256-bit tokens
        self.assertEqual(len(tokens), count)

    def test_init_secrets_env_idempotency_and_preservation(self):
        with tempfile.TemporaryDirectory(prefix="mios-sec-idemp-") as tmpdir:
            sec_file = os.path.join(tmpdir, "secrets.env")
            hardener = ec1_sec.QuadletSecretsHardener(secrets_dir=tmpdir)

            # 1. First initialization on empty file
            first_run = hardener.init_secrets_env(secrets_file=sec_file)
            self.assertIn("POSTGRES_PASSWORD", first_run)
            self.assertIn("K3S_TOKEN", first_run)
            first_token = first_run["K3S_TOKEN"]

            # 2. Run 10 consecutive times — all tokens MUST remain identical
            for _ in range(10):
                subsequent_run = hardener.init_secrets_env(secrets_file=sec_file)
                self.assertEqual(subsequent_run, first_run)
                self.assertEqual(subsequent_run["K3S_TOKEN"], first_token)

            # 3. Add custom complex secrets with =, #, and spaces
            with open(sec_file, "a", encoding="utf-8") as f:
                f.write("CUSTOM_CONN_STR=postgresql://user:p=w@d/db?ssl=true\n")
                f.write("SPACED_KEY = spaced_value_123 \n")

            custom_run = hardener.init_secrets_env(secrets_file=sec_file)
            self.assertEqual(custom_run["CUSTOM_CONN_STR"], "postgresql://user:p=w@d/db?ssl=true")
            self.assertEqual(custom_run["SPACED_KEY"], "spaced_value_123")
            self.assertEqual(custom_run["K3S_TOKEN"], first_token)

    def test_permission_hardening_scan(self):
        with tempfile.TemporaryDirectory(prefix="mios-perm-scan-") as tmpdir:
            f1 = os.path.join(tmpdir, "app.env")
            f2 = os.path.join(tmpdir, "db.secret")
            f3 = os.path.join(tmpdir, "ignored.txt")

            for f in [f1, f2, f3]:
                with open(f, "w") as fp:
                    fp.write("KEY=VAL\n")
                os.chmod(f, 0o644)

            hardener = ec1_sec.QuadletSecretsHardener(secrets_dir=tmpdir)
            fixed = hardener.audit_and_harden_permissions(tmpdir)

            self.assertIn(f1, fixed)
            self.assertIn(f2, fixed)
            self.assertNotIn(f3, fixed)

class ec1_TestAdversarialLookingGlass(unittest.TestCase):
    """Stress tests on Looking Glass IVSHMEM sizing, XML generation, and verification."""

    def test_xml_generation_standard_and_extreme_sizes(self):
        sizes = [16, 32, 64, 128, 256, 512, 1024, 0, -64, 33]
        for sz in sizes:
            lg_mgr = ec1_lg.LookingGlassManager(size_mb=sz)
            xml = lg_mgr.generate_ivshmem_xml()
            self.assertIn('<shmem name="looking-glass">', xml)
            self.assertIn('<model type="ivshmem-plain"/>', xml)
            self.assertIn(f'<size unit="M">{sz}</size>', xml)

    def test_mock_and_real_verification(self):
        lg_mgr = ec1_lg.LookingGlassManager(shm_path="/nonexistent/shm/file", device_node="/nonexistent/kvmfr")
        # Mock mode passes
        res_mock = lg_mgr.verify_all(mock=True)
        self.assertEqual(res_mock["status"], "pass")

        # Non-mock mode on nonexistent files fails (on POSIX systems)
        if os.name != "nt":
            res_real = lg_mgr.verify_all(mock=False)
            self.assertEqual(res_real["status"], "fail")
            self.assertEqual(res_real["checks"]["shm_allocation"], "fail")
            self.assertEqual(res_real["checks"]["kvmfr_device"], "fail")


# ==== from tests/test-sec.py (prefix es_) ====
"""Automated unit test suite for MiOS Hardware Entropy Seeder."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "usr", "libexec", "mios", "sec"))

from entropy_seed import HardwareEntropySeeder

class es_TestEntropySeed(unittest.TestCase):
    def setUp(self):
        self.seeder = HardwareEntropySeeder(dry_run=True)

    def test_multi_source_entropy_harvesting(self):
        """Test harvesting combines CPU RDSEED, TPM 2.0 TRNG, and JitterEntropy."""
        res = self.seeder.harvest_and_seed_entropy(mock_bytes_count=512)
        self.assertEqual(len(res.sources_harvested), 3)
        self.assertEqual(res.bits_injected, 512 * 8)

    def test_shannon_entropy_density_and_compliance(self):
        """Test conditioned entropy achieves >7.85 bits/byte and passes NIST compliance."""
        res = self.seeder.harvest_and_seed_entropy(mock_bytes_count=2048)
        self.assertTrue(res.is_nist_compliant)
        self.assertGreaterEqual(res.shannon_entropy, 7.85)


# ==== from tests/test-sec.py (prefix fe_) ====
"""Unit and integration test suite for Fido2EnrollEngine and fido2_enroll CLI."""


import importlib.util
import json
import os
import sys
import unittest
from unittest.mock import patch

fe__HERE = os.path.dirname(os.path.abspath(__file__))
fe__ROOT = os.path.normpath(os.path.join(fe__HERE, ".."))
fe__TARGET_PATH = os.path.join(fe__ROOT, "usr", "libexec", "mios", "sec", "fido2_enroll.py")

fe_spec = importlib.util.spec_from_file_location("fido2_enroll", fe__TARGET_PATH)
if fe_spec and fe_spec.loader:
    fido2_enroll = importlib.util.module_from_spec(fe_spec)
    sys.modules[fe_spec.name] = fido2_enroll
    fe_spec.loader.exec_module(fido2_enroll)
else:
    raise ImportError(f"Could not load module from {fe__TARGET_PATH}")

class fe_TestFido2Enroll(unittest.TestCase):
    """Test suite for FIDO2 token discovery, LUKS2 verification, and keyslot enrollment."""

    def test_discover_tokens_mock(self):
        engine = fido2_enroll.Fido2EnrollEngine(mock=True)
        tokens = engine.discover_tokens()
        self.assertGreaterEqual(len(tokens), 1)
        self.assertTrue(any("YubiKey" in t.product_name for t in tokens))
        self.assertTrue(any(t.has_up for t in tokens))

    def test_inspect_device_luks2(self):
        engine = fido2_enroll.Fido2EnrollEngine(mock=True)
        res = engine.inspect_device("/dev/sdb2")
        self.assertEqual(res.status, "ok")
        self.assertTrue(res.is_luks2)
        self.assertEqual(res.label, "MiOS-Cat-Storage")
        self.assertIn("uuid", res.to_dict())
        self.assertFalse(res.fido2_enrolled)

    def test_inspect_device_already_enrolled(self):
        engine = fido2_enroll.Fido2EnrollEngine(mock=True)
        res = engine.inspect_device("/dev/sdc1")
        self.assertEqual(res.status, "ok")
        self.assertTrue(res.is_luks2)
        self.assertTrue(res.fido2_enrolled)
        self.assertGreaterEqual(len(res.keyslots), 2)

    def test_inspect_device_non_luks2(self):
        engine = fido2_enroll.Fido2EnrollEngine(mock=True)
        res = engine.inspect_device("/dev/sdd1")
        self.assertEqual(res.status, "not_luks2")
        self.assertFalse(res.is_luks2)

    def test_inspect_device_missing_raises_or_errors(self):
        engine = fido2_enroll.Fido2EnrollEngine(mock=True)
        res = engine.inspect_device("/dev/nonexistent")
        self.assertEqual(res.status, "error")

    def test_enroll_fido2_token_success(self):
        engine = fido2_enroll.Fido2EnrollEngine(mock=True)
        res = engine.enroll_fido2(
            device_path="/dev/sdb2",
            fido2_device="/dev/hidraw0",
            require_pin=True,
            require_touch=True,
            require_user_verification=False,
            recovery_key=True,
        )
        self.assertEqual(res.status, "success")
        self.assertEqual(res.device, "/dev/sdb2")
        self.assertIsNotNone(res.keyslot)
        self.assertIsNotNone(res.recovery_key)
        self.assertIn("systemd-cryptenroll", res.command_executed)
        self.assertIn("--fido2-with-client-pin=yes", res.command_executed)
        self.assertIn("--recovery-key", res.command_executed)

        # Inspect to verify keyslot state
        post_status = engine.inspect_device("/dev/sdb2")
        self.assertTrue(post_status.fido2_enrolled)
        self.assertTrue(post_status.recovery_enrolled)

    def test_enroll_fido2_non_luks2_fails(self):
        engine = fido2_enroll.Fido2EnrollEngine(mock=True)
        res = engine.enroll_fido2(device_path="/dev/sdd1")
        self.assertEqual(res.status, "error")
        self.assertIn("not a valid LUKS2 volume", res.message)

    def test_wipe_keyslot_fido2(self):
        engine = fido2_enroll.Fido2EnrollEngine(mock=True)
        # sdc1 has FIDO2 keyslot 1
        wipe_res = engine.wipe_keyslot("/dev/sdc1", wipe_spec="fido2")
        self.assertEqual(wipe_res["status"], "success")
        self.assertIn(1, wipe_res["wiped_slots"])

        post_status = engine.inspect_device("/dev/sdc1")
        self.assertFalse(post_status.fido2_enrolled)

    def test_wipe_keyslot_by_id(self):
        engine = fido2_enroll.Fido2EnrollEngine(mock=True)
        wipe_res = engine.wipe_keyslot("/dev/sdc1", wipe_spec="0")
        self.assertEqual(wipe_res["status"], "success")
        self.assertIn(0, wipe_res["wiped_slots"])

    def test_test_unlock_mock_success_and_failure(self):
        engine = fido2_enroll.Fido2EnrollEngine(mock=True)
        # sdc1 has FIDO2 enrolled
        unlock_ok = engine.test_unlock("/dev/sdc1")
        self.assertEqual(unlock_ok["status"], "success")
        self.assertTrue(unlock_ok["unlocked"])

        # sdb2 does not have FIDO2 initially
        unlock_fail = engine.test_unlock("/dev/sdb2")
        self.assertEqual(unlock_fail["status"], "error")
        self.assertFalse(unlock_fail["unlocked"])

    def test_cli_list_tokens(self):
        test_args = ["fido2_enroll.py", "--list-tokens", "--mock", "--json"]
        with patch.object(sys, "argv", test_args):
            exit_code = fido2_enroll.main()
            self.assertEqual(exit_code, 0)

    def test_cli_status(self):
        test_args = ["fido2_enroll.py", "--device", "/dev/sdb2", "--status", "--mock", "--json"]
        with patch.object(sys, "argv", test_args):
            exit_code = fido2_enroll.main()
            self.assertEqual(exit_code, 0)

    def test_cli_enroll(self):
        test_args = [
            "fido2_enroll.py",
            "--device", "/dev/sdb2",
            "--fido2-device", "auto",
            "--require-pin",
            "--recovery-key",
            "--mock",
            "--json",
        ]
        with patch.object(sys, "argv", test_args):
            exit_code = fido2_enroll.main()
            self.assertEqual(exit_code, 0)

    def test_cli_wipe_slot(self):
        test_args = [
            "fido2_enroll.py",
            "--device", "/dev/sdc1",
            "--wipe-slot", "fido2",
            "--mock",
            "--json",
        ]
        with patch.object(sys, "argv", test_args):
            exit_code = fido2_enroll.main()
            self.assertEqual(exit_code, 0)

    def test_cli_test_unlock(self):
        test_args = [
            "fido2_enroll.py",
            "--device", "/dev/sdc1",
            "--test-unlock",
            "--mock",
            "--json",
        ]
        with patch.object(sys, "argv", test_args):
            exit_code = fido2_enroll.main()
            self.assertEqual(exit_code, 0)

def fe_main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(fe_TestFido2Enroll)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# ==== from tests/test-sec.py (prefix fhs_) ====
"""Unit and integration tests for FIDO2SecurityManager."""


import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

fhs__HERE = os.path.dirname(os.path.abspath(__file__))
fhs__ROOT = os.path.normpath(os.path.join(fhs__HERE, ".."))
fhs__TARGET_PATH = os.path.join(fhs__ROOT, "usr", "libexec", "mios", "sec", "fido2_manager.py")

fhs_spec = importlib.util.spec_from_file_location("fido2_manager", fhs__TARGET_PATH)
if fhs_spec and fhs_spec.loader:
    fido2_manager = importlib.util.module_from_spec(fhs_spec)
    sys.modules[fhs_spec.name] = fido2_manager
    fhs_spec.loader.exec_module(fido2_manager)
else:
    raise ImportError(f"Could not load module from {fhs__TARGET_PATH}")

class fhs_TestFIDO2SecurityManager(unittest.TestCase):
    """Test suite for FIDO2 device discovery, PAM U2F enrollment, and SSH SK key synthesis."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="mios-test-fido2-")
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_discover_devices_mock(self):
        mgr = fido2_manager.FIDO2SecurityManager(mock=True)
        devs = mgr.discover_devices()
        self.assertEqual(len(devs), 2)
        self.assertEqual(devs[0].manufacturer, "Yubico")
        self.assertTrue(devs[0].pin_required)
        self.assertEqual(devs[1].manufacturer, "SoloKeys")

    def test_enroll_pam_u2f_mock(self):
        u2f_out = self.root / "u2f_keys"
        mgr = fido2_manager.FIDO2SecurityManager(mock=True)

        ok, details = mgr.enroll_pam_u2f(username="operator", pin_enforced=True, output_file=str(u2f_out))
        self.assertTrue(ok)
        self.assertEqual(details["username"], "operator")
        self.assertTrue(details["pin_enforced"])
        self.assertTrue(u2f_out.exists())

        content = u2f_out.read_text(encoding="utf-8")
        self.assertIn("operator:mock_key_handle", content)
        self.assertIn("+pin", content)

    def test_generate_ssh_sk_mock(self):
        ssh_out = self.root / ".ssh"
        mgr = fido2_manager.FIDO2SecurityManager(mock=True)

        ok, details = mgr.generate_ssh_sk(output_dir=str(ssh_out))
        self.assertTrue(ok)
        self.assertTrue(details["resident"])
        self.assertTrue(os.path.exists(details["private_key_path"]))
        self.assertTrue(os.path.exists(details["public_key_path"]))

    def test_cli_execution_discover_mock(self):
        test_args = ["fido2_manager.py", "--discover", "--mock", "--json"]
        with patch.object(sys, "argv", test_args):
            exit_code = fido2_manager.main()
            self.assertEqual(exit_code, 0)

    def test_cli_execution_enroll_pam_mock(self):
        out_path = str(self.root / "cli_u2f_keys")
        test_args = ["fido2_manager.py", "--enroll-pam", "--username", "testuser", "--output", out_path, "--mock", "--json"]
        with patch.object(sys, "argv", test_args):
            exit_code = fido2_manager.main()
            self.assertEqual(exit_code, 0)
            self.assertTrue(os.path.exists(out_path))

    def test_cli_execution_generate_ssh_sk_mock(self):
        out_dir = str(self.root / "cli_ssh")
        test_args = ["fido2_manager.py", "--generate-ssh-sk", "--output", out_dir, "--mock", "--json"]
        with patch.object(sys, "argv", test_args):
            exit_code = fido2_manager.main()
            self.assertEqual(exit_code, 0)

def fhs_main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(fhs_TestFIDO2SecurityManager)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# ==== from tests/test-sec.py (prefix gg_) ====
"""Unit and integration test suite for GreenbootGateEngine and greenboot_gate CLI."""


import importlib.util
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

gg__HERE = os.path.dirname(os.path.abspath(__file__))
gg__ROOT = os.path.normpath(os.path.join(gg__HERE, ".."))
gg__TARGET_PATH = os.path.join(gg__ROOT, "usr", "libexec", "mios", "sec", "greenboot_gate.py")

gg_spec = importlib.util.spec_from_file_location("greenboot_gate", gg__TARGET_PATH)
if gg_spec and gg_spec.loader:
    greenboot_gate = importlib.util.module_from_spec(gg_spec)
    sys.modules[gg_spec.name] = greenboot_gate
    gg_spec.loader.exec_module(greenboot_gate)
else:
    raise ImportError(f"Could not load module from {gg__TARGET_PATH}")

class gg_TestGreenbootGate(unittest.TestCase):
    """Test suite for Greenboot post-bake health verification, rollback triggering, and quarantine recording."""

    def test_load_history_mock(self):
        engine = greenboot_gate.GreenbootGateEngine(mock=True)
        history = engine.load_history()
        self.assertEqual(history["total_bakes"], 1)
        self.assertIsNotNone(history["latest_bake"])

    def test_detect_pending_bake_mock(self):
        engine = greenboot_gate.GreenbootGateEngine(mock=True)
        pending = engine.detect_pending_bake()
        self.assertIsNotNone(pending)
        self.assertEqual(pending["bake_id"], "mock-bake-01")

    def test_check_service_health_success_mock(self):
        engine = greenboot_gate.GreenbootGateEngine(mock=True, mock_failure=False)
        report = engine.check_service_health()
        self.assertTrue(report["healthy"])
        self.assertEqual(len(report["failing_services"]), 0)
        self.assertTrue(report["endpoint_healthy"])

    def test_check_service_health_failure_mock(self):
        engine = greenboot_gate.GreenbootGateEngine(mock=True, mock_failure=True)
        report = engine.check_service_health()
        self.assertFalse(report["healthy"])
        self.assertIn("agent-pipe.service", report["failing_services"])
        self.assertFalse(report["endpoint_healthy"])

    def test_execute_rollback_mock(self):
        engine = greenboot_gate.GreenbootGateEngine(mock=True)
        res = engine.execute_rollback()
        self.assertEqual(res["status"], "rollback_executed")
        self.assertIn("bootc rollback", res["command"])

    def test_quarantine_diffs_recording(self):
        with tempfile.TemporaryDirectory(prefix="mios-greenboot-quarantine-") as tmpdir:
            quarantine_file = os.path.join(tmpdir, "quarantine.json")
            engine = greenboot_gate.GreenbootGateEngine(quarantine_path=quarantine_file, mock=True)
            bake_record = {
                "bake_id": "test-fail-bake",
                "commit_sha": "bad123",
                "image_tag": "localhost/mios:bad123",
                "staged_files": ["etc/pam.d/system-auth"],
            }
            entry = engine.quarantine_diffs(
                bake_record,
                reason="PAM segmentation fault",
                failing_services=["systemd-logind.service"],
            )
            self.assertEqual(entry["bake_id"], "test-fail-bake")
            self.assertTrue(os.path.isfile(quarantine_file))
            with open(quarantine_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(data["total_quarantined"], 1)

    def test_verify_gate_healthy_pass(self):
        with tempfile.TemporaryDirectory(prefix="mios-greenboot-pass-") as tmpdir:
            hist_file = os.path.join(tmpdir, "history.json")
            engine = greenboot_gate.GreenbootGateEngine(
                history_path=hist_file,
                mock=True,
                mock_failure=False,
            )
            res = engine.verify_gate()
            self.assertEqual(res["status"], "pass")
            self.assertTrue(res["health"]["healthy"])

    def test_verify_gate_failure_triggers_rollback_and_quarantine(self):
        with tempfile.TemporaryDirectory(prefix="mios-greenboot-fail-") as tmpdir:
            hist_file = os.path.join(tmpdir, "history.json")
            quarantine_file = os.path.join(tmpdir, "quarantine.json")
            engine = greenboot_gate.GreenbootGateEngine(
                history_path=hist_file,
                quarantine_path=quarantine_file,
                mock=True,
                mock_failure=True,
            )
            res = engine.verify_gate()
            self.assertEqual(res["status"], "failed_rolled_back")
            self.assertIn("rollback", res)
            self.assertIn("quarantine", res)
            self.assertTrue(os.path.isfile(quarantine_file))

    def test_cli_check_mock(self):
        test_args = ["greenboot_gate.py", "--check", "--mock", "--json"]
        with patch.object(sys, "argv", test_args):
            exit_code = greenboot_gate.main()
            self.assertEqual(exit_code, 0)

    def test_cli_status_mock(self):
        test_args = ["greenboot_gate.py", "--status", "--mock", "--json"]
        with patch.object(sys, "argv", test_args):
            exit_code = greenboot_gate.main()
            self.assertEqual(exit_code, 0)

def gg_main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(gg_TestGreenbootGate)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# ==== from tests/test-sec.py (prefix gs_) ====
"""Unit and integration test suite for GrypeScanner and CLI."""


import importlib.util
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

gs__HERE = os.path.dirname(os.path.abspath(__file__))
gs__ROOT = os.path.normpath(os.path.join(gs__HERE, ".."))
gs__TARGET_PATH = os.path.join(gs__ROOT, "usr", "libexec", "mios", "sec", "grype_scan.py")

gs_spec = importlib.util.spec_from_file_location("grype_scan", gs__TARGET_PATH)
if gs_spec and gs_spec.loader:
    grype_scan = importlib.util.module_from_spec(gs_spec)
    sys.modules[gs_spec.name] = grype_scan
    gs_spec.loader.exec_module(grype_scan)
else:
    raise ImportError(f"Could not load module from {gs__TARGET_PATH}")

class gs_TestGrypeScan(unittest.TestCase):
    """Test suite for Grype vulnerability report parsing, policy evaluation, exemptions, and SARIF export."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="mios-test-grype-")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_run_scan_mock(self):
        scanner = grype_scan.GrypeScanner(mock=True)
        raw_res = scanner.run_scan("/")
        self.assertIn("matches", raw_res)
        self.assertGreaterEqual(len(raw_res["matches"]), 1)

    def test_parse_vulnerabilities_normalization(self):
        scanner = grype_scan.GrypeScanner(mock=True)
        sample_output = {
            "matches": [
                {
                    "vulnerability": {
                        "id": "CVE-2026-9999",
                        "severity": "Critical",
                        "description": "Critical remote code execution vulnerability",
                        "fix": {"versions": ["2.0.1"], "state": "fixed"},
                    },
                    "artifact": {
                        "name": "core-daemon",
                        "version": "2.0.0",
                        "type": "rpm",
                    },
                }
            ]
        }
        vulns = scanner.parse_vulnerabilities(sample_output)
        self.assertEqual(len(vulns), 1)
        self.assertEqual(vulns[0]["id"], "CVE-2026-9999")
        self.assertEqual(vulns[0]["severity"], "CRITICAL")
        self.assertEqual(vulns[0]["package"], "core-daemon")
        self.assertEqual(vulns[0]["fix_state"], "fixed")

    def test_evaluate_policy_blocked_on_unexempted_high(self):
        scanner = grype_scan.GrypeScanner(mock=True)
        vulns = [
            {
                "id": "CVE-2026-1001",
                "severity": "HIGH",
                "package": "libssl",
                "version": "3.0.0",
                "fix_state": "fixed",
                "fix_versions": ["3.0.1"],
            }
        ]
        # Without exemption -> blocked
        res_blocked = scanner.evaluate_policy(vulns, max_severity="HIGH", fail_on_fixable=True, exemptions=[])
        self.assertEqual(res_blocked["status"], "fail")
        self.assertTrue(res_blocked["blocked"])
        self.assertEqual(len(res_blocked["actionable_cves"]), 1)

        # With exemption -> passes
        res_exempt = scanner.evaluate_policy(vulns, max_severity="HIGH", fail_on_fixable=True, exemptions=["CVE-2026-1001"])
        self.assertEqual(res_exempt["status"], "pass")
        self.assertFalse(res_exempt["blocked"])
        self.assertIn("CVE-2026-1001", res_exempt["exempted_cves"])

    def test_format_sarif_schema(self):
        scanner = grype_scan.GrypeScanner(mock=True)
        vulns = [
            {
                "id": "CVE-2026-1001",
                "severity": "HIGH",
                "package": "libcrypto",
                "version": "1.0",
                "description": "Buffer overflow",
            }
        ]
        sarif = scanner.format_sarif(vulns)
        self.assertEqual(sarif["version"], "2.1.0")
        self.assertIn("runs", sarif)
        self.assertEqual(len(sarif["runs"][0]["results"]), 1)
        self.assertEqual(sarif["runs"][0]["results"][0]["ruleId"], "CVE-2026-1001")

    def test_cli_execution_with_exemptions(self):
        test_args = [
            "grype_scan.py",
            "--target", "/",
            "--severity", "HIGH",
            "--exemptions", "CVE-2026-1001",
            "--mock",
            "--json",
        ]
        with patch.object(sys, "argv", test_args):
            exit_code = grype_scan.main()
            self.assertEqual(exit_code, 0)

    def test_cli_execution_sarif_export(self):
        sarif_file = os.path.join(self.temp_dir.name, "report.sarif")
        test_args = [
            "grype_scan.py",
            "--target", "/",
            "--exemptions", "CVE-2026-1001",
            "--sarif-out", sarif_file,
            "--mock",
            "--json",
        ]
        with patch.object(sys, "argv", test_args):
            exit_code = grype_scan.main()
            self.assertEqual(exit_code, 0)
            self.assertTrue(os.path.exists(sarif_file))

def gs_main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(gs_TestGrypeScan)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# ==== from tests/test-sec.py (prefix ha_) ====
"""
Automated unit tests for MiOS HITL Permission Escalation and Approval Engine (SEC-06).

Validates high-risk command interception, safe command passthrough, approval token cryptography,
rejection handling, TTL expiration, persistence, and CLI operations.
"""


import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

ha__HERE = os.path.dirname(os.path.abspath(__file__))
ha__ROOT = os.path.normpath(os.path.join(ha__HERE, ".."))
sys.path.insert(0, os.path.join(ha__ROOT, "usr", "libexec", "mios", "sec"))

try:
    import approval
except ImportError:
    import importlib.util
    _spec = importlib.util.spec_from_file_location(
        "approval",
        os.path.join(ha__ROOT, "usr", "libexec", "mios", "sec", "approval.py")
    )
    approval = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(approval)

class ha_TestHitlApproval(unittest.TestCase):
    """Unit test suite for ApprovalEngine and ApprovalRequest lifecycle."""

    def setUp(self) -> None:
        self.engine = approval.ApprovalEngine()

    def test_dangerous_command_detection(self) -> None:
        """Verify that known destructive and system-modifying commands trigger approval requirement."""
        dangerous_commands = [
            "rm -rf /var/lib/data",
            "rm -rf /",
            "rm -r /home/user/docs",
            "rm --recursive --force /tmp/cache",
            "mkfs.ext4 /dev/nvme0n1",
            "mkfs.xfs /dev/sda1",
            "fdisk /dev/sda",
            "gdisk /dev/nvme0n1",
            "parted /dev/sda mklabel gpt",
            "sfdisk /dev/sdb",
            "wipefs -a /dev/sda1",
            "dd if=/dev/zero of=/dev/sda bs=1M",
            "dd if=/dev/urandom of=/dev/nvme0n1",
            "bootc switch quay.io/ublue-os/ucore-hci:latest",
            "bootc rollback",
            "bootc edit",
            "cryptsetup luksFormat /dev/sda2",
            "cryptsetup luksKillSlot /dev/sda2 1",
            "cryptsetup luksErase /dev/sda2",
            "iptables -F",
            "iptables --flush",
            "ip6tables -F",
            "iptables -X",
            "nft flush ruleset",
            "nft delete table inet filter",
            "lvremove -f /dev/vg0/lv_root",
            "vgremove vg0",
            "pvremove /dev/sdb",
            "btrfs subvolume delete /mnt/data/@snapshots",
            "zpool destroy tank",
            "zfs destroy tank/data",
            "reboot",
            "shutdown -h now",
            "poweroff",
            "init 0",
            "init 6",
        ]
        for cmd in dangerous_commands:
            with self.subTest(command=cmd):
                self.assertTrue(
                    self.engine.requires_approval(cmd),
                    f"Expected command to require approval: {cmd}"
                )

    def test_safe_command_passthrough(self) -> None:
        """Verify that benign diagnostic, inspection, and development commands pass without escalation."""
        safe_commands = [
            "ls -la /usr/share",
            "cat /etc/os-release",
            "echo 'hello world'",
            "git status",
            "git log -n 5",
            "python tests/test-suite.py",
            "grep -rn 'pattern' /usr/share/mios",
            "find /var/log -name '*.log'",
            "df -h",
            "uptime",
            "ps aux",
            "systemctl status mios-llm-light",
            "",
            "   ",
        ]
        for cmd in safe_commands:
            with self.subTest(command=cmd):
                self.assertFalse(
                    self.engine.requires_approval(cmd),
                    f"Expected benign command to pass without approval: {cmd}"
                )

    def test_custom_pattern_configuration(self) -> None:
        """Verify custom pattern lists and dynamic pattern addition."""
        custom_engine = approval.ApprovalEngine(patterns=[r"^dangerous-custom-tool\b.*"])
        self.assertTrue(custom_engine.requires_approval("dangerous-custom-tool --wipe"))
        self.assertFalse(custom_engine.requires_approval("rm -rf /tmp"))

        custom_engine.add_pattern(r"^rm\s+-rf.*")
        self.assertTrue(custom_engine.requires_approval("rm -rf /tmp"))

    def test_request_creation_and_fields(self) -> None:
        """Verify request lifecycle creation, initial status, and field assignment."""
        req = self.engine.create_request(
            tool_name="bash_exec",
            command="rm -rf /tmp/scratch",
            reason="Cleanup temporary build artifacts",
            ttl_seconds=300,
        )
        self.assertTrue(req.request_id.startswith("req-"))
        self.assertEqual(req.tool_name, "bash_exec")
        self.assertEqual(req.command, "rm -rf /tmp/scratch")
        self.assertEqual(req.reason, "Cleanup temporary build artifacts")
        self.assertEqual(req.status, approval.Status.PENDING)
        self.assertEqual(req.status.value, "PENDING")
        self.assertEqual(req.ttl_seconds, 300)
        self.assertGreater(req.expires_at, req.created_at)

        fetched = self.engine.get_request(req.request_id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.request_id, req.request_id)

    def test_approval_token_issuance_and_validation(self) -> None:
        """Verify approval workflow, token generation, and authorization check."""
        req = self.engine.create_request(
            tool_name="bash_exec",
            command="wipefs -a /dev/sdb1",
        )
        self.assertFalse(self.engine.is_executable(req.request_id))

        token = self.engine.approve(req.request_id, operator="sec_operator")
        self.assertIsInstance(token, str)
        self.assertGreater(len(token), 32)

        self.assertEqual(req.status, approval.Status.APPROVED)
        self.assertEqual(req.operator, "sec_operator")
        self.assertIsNotNone(req.approved_at)
        self.assertEqual(req.token, token)

        # Validate token and execution check
        self.assertTrue(self.engine.validate_token(req.request_id, token))
        self.assertTrue(self.engine.is_executable(req.request_id))

    def test_token_cryptographic_verification_and_tampering(self) -> None:
        """Verify cryptographic rejection of forged, tampered, or mismatched tokens."""
        req1 = self.engine.create_request(tool_name="bash_exec", command="mkfs.ext4 /dev/sdb")
        token1 = self.engine.approve(req1.request_id, operator="admin")

        req2 = self.engine.create_request(tool_name="bash_exec", command="mkfs.ext4 /dev/sdc")
        token2 = self.engine.approve(req2.request_id, operator="admin")

        # Mismatched token between requests
        self.assertFalse(self.engine.validate_token(req1.request_id, token2))
        self.assertFalse(self.engine.validate_token(req2.request_id, token1))

        # Tampered token string
        tampered_token = token1[:-4] + "AAAA"
        self.assertFalse(self.engine.validate_token(req1.request_id, tampered_token))

        # Foreign secret key verification rejection
        foreign_engine = approval.ApprovalEngine()
        self.assertFalse(foreign_engine.validate_token(req1.request_id, token1))

        # Null / Empty values
        self.assertFalse(self.engine.validate_token("", token1))
        self.assertFalse(self.engine.validate_token(req1.request_id, ""))
        self.assertFalse(self.engine.validate_token("req-nonexistent", token1))

    def test_rejection_behavior(self) -> None:
        """Verify request rejection, reason tracking, and execution blocking."""
        req = self.engine.create_request(
            tool_name="bash_exec",
            command="dd if=/dev/zero of=/dev/sda",
        )
        ok = self.engine.reject(req.request_id, reason="Prohibited raw disk wipe")
        self.assertTrue(ok)
        self.assertEqual(req.status, approval.Status.REJECTED)
        self.assertEqual(req.rejection_reason, "Prohibited raw disk wipe")
        self.assertIsNotNone(req.rejected_at)

        self.assertFalse(self.engine.is_executable(req.request_id))

        # Attempting to approve a rejected request must fail
        with self.assertRaises(ValueError):
            self.engine.approve(req.request_id, operator="admin")

        # Second rejection should return False
        self.assertFalse(self.engine.reject(req.request_id))

    def test_ttl_expiration_behavior(self) -> None:
        """Verify that requests expire after TTL and cannot be approved or executed."""
        req = self.engine.create_request(
            tool_name="bash_exec",
            command="bootc rollback",
            ttl_seconds=0,
        )
        # Immediate expiration check
        self.assertTrue(req.is_expired())
        self.assertEqual(req.status, approval.Status.EXPIRED)

        # Cannot approve expired request
        with self.assertRaises(ValueError):
            self.engine.approve(req.request_id, operator="admin")

        self.assertFalse(self.engine.is_executable(req.request_id))

    def test_expired_approved_token_invalidation(self) -> None:
        """Verify that an approved token becomes invalid once the request expires."""
        req = self.engine.create_request(
            tool_name="bash_exec",
            command="systemctl stop firewalld",
            ttl_seconds=1,
        )
        token = self.engine.approve(req.request_id, operator="admin")
        self.assertTrue(self.engine.validate_token(req.request_id, token))

        # Force expiration
        req.expires_at = time.time() - 5
        self.assertTrue(req.is_expired())
        self.assertFalse(self.engine.validate_token(req.request_id, token))
        self.assertFalse(self.engine.is_executable(req.request_id))

    def test_list_and_purge_requests(self) -> None:
        """Verify filtering and purging of approval requests."""
        engine = approval.ApprovalEngine()
        r1 = engine.create_request("bash", "cmd1", ttl_seconds=100)
        r2 = engine.create_request("bash", "cmd2", ttl_seconds=100)
        r3 = engine.create_request("bash", "cmd3", ttl_seconds=0)

        engine.approve(r1.request_id, "admin")
        engine.reject(r2.request_id, "test reject")

        all_reqs = engine.list_requests()
        self.assertEqual(len(all_reqs), 3)

        approved_reqs = engine.list_requests(status=approval.Status.APPROVED)
        self.assertEqual(len(approved_reqs), 1)
        self.assertEqual(approved_reqs[0].request_id, r1.request_id)

        rejected_reqs = engine.list_requests(status="REJECTED")
        self.assertEqual(len(rejected_reqs), 1)
        self.assertEqual(rejected_reqs[0].request_id, r2.request_id)

        expired_reqs = engine.list_requests(status=approval.Status.EXPIRED)
        self.assertEqual(len(expired_reqs), 1)
        self.assertEqual(expired_reqs[0].request_id, r3.request_id)

        purged_count = engine.purge_expired()
        self.assertEqual(purged_count, 1)
        self.assertEqual(len(engine.list_requests()), 2)

    def test_state_persistence_and_reload(self) -> None:
        """Verify JSON state persistence across engine instances."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = os.path.join(tmpdir, "approval_state.json")
            eng1 = approval.ApprovalEngine(state_file=state_file)
            req = eng1.create_request("bash", "fdisk /dev/nvme0n1", ttl_seconds=300)
            token = eng1.approve(req.request_id, operator="admin_user")

            eng2 = approval.ApprovalEngine(state_file=state_file)
            req_loaded = eng2.get_request(req.request_id)
            self.assertIsNotNone(req_loaded)
            self.assertEqual(req_loaded.status, approval.Status.APPROVED)
            self.assertEqual(req_loaded.operator, "admin_user")
            self.assertTrue(eng2.validate_token(req.request_id, token))
            self.assertTrue(eng2.is_executable(req.request_id))

    def test_cli_operations(self) -> None:
        """Verify CLI subcommands and JSON output format."""
        script_path = os.path.join(ha__ROOT, "usr", "libexec", "mios", "sec", "approval.py")

        # Test --check
        res_risky = subprocess.run(
            [sys.executable, script_path, "--check", "rm -rf /var", "--json"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(res_risky.returncode, 0)
        data_risky = json.loads(res_risky.stdout)
        self.assertTrue(data_risky["requires_approval"])

        res_safe = subprocess.run(
            [sys.executable, script_path, "--check", "ls -l /tmp", "--json"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(res_safe.returncode, 1)
        data_safe = json.loads(res_safe.stdout)
        self.assertFalse(data_safe["requires_approval"])

        # Test full CLI lifecycle with state-file
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = os.path.join(tmpdir, "cli_state.json")

            # 1. Create request
            res_req = subprocess.run(
                [
                    sys.executable, script_path,
                    "--request",
                    "--command", "wipefs -a /dev/sda",
                    "--tool", "bash_exec",
                    "--reason", "CLI test wipe",
                    "--ttl", "300",
                    "--state-file", state_file,
                    "--json",
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            req_data = json.loads(res_req.stdout)
            req_id = req_data["request_id"]
            self.assertEqual(req_data["status"], "PENDING")

            # 2. Query status
            res_status = subprocess.run(
                [sys.executable, script_path, "--status", req_id, "--state-file", state_file, "--json"],
                capture_output=True,
                text=True,
                check=True,
            )
            status_data = json.loads(res_status.stdout)
            self.assertEqual(status_data["request_id"], req_id)
            self.assertEqual(status_data["status"], "PENDING")

            # 3. Approve request
            res_app = subprocess.run(
                [
                    sys.executable, script_path,
                    "--approve", req_id,
                    "--operator", "cli_admin",
                    "--state-file", state_file,
                    "--json",
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            app_data = json.loads(res_app.stdout)
            self.assertEqual(app_data["status"], "APPROVED")
            token = app_data["token"]

            # 4. Validate token
            res_val = subprocess.run(
                [
                    sys.executable, script_path,
                    "--validate",
                    "--request-id", req_id,
                    "--token", token,
                    "--state-file", state_file,
                    "--json",
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            val_data = json.loads(res_val.stdout)
            self.assertTrue(val_data["valid"])

            # 5. List requests
            res_list = subprocess.run(
                [sys.executable, script_path, "--list", "--state-file", state_file, "--json"],
                capture_output=True,
                text=True,
                check=True,
            )
            list_data = json.loads(res_list.stdout)
            self.assertEqual(len(list_data), 1)
            self.assertEqual(list_data[0]["request_id"], req_id)

def ha_main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(ha_TestHitlApproval)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# ==== from tests/test-sec.py (prefix jf_) ====
"""Automated unit test suite for MiOS Journal FSS Manager."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "usr", "libexec", "mios", "sec"))

from journal_fss import JournalFSSManager

class jf_TestJournalFSS(unittest.TestCase):
    def setUp(self):
        self.mgr = JournalFSSManager(interval_minutes=15, dry_run=True)

    def test_fss_key_setup_and_tpm_sealing(self):
        """Test initializing FSS generates 15-minute epoch key sealed to TPM."""
        res = self.mgr.setup_fss_keys()
        self.assertEqual(res.interval_minutes, 15)
        self.assertTrue(res.is_sealed_to_tpm)
        self.assertTrue(res.fss_key_id.startswith("fss_"))

    def test_tamper_detection_flags_altered_log_records(self):
        """Test modifying single record byte causes verification failure."""
        logs = [f"Log entry {i} timestamp" for i in range(10)]
        self.assertTrue(self.mgr.verify_journal_integrity(logs))
        # Tamper record 5
        self.assertFalse(self.mgr.verify_journal_integrity(logs, tamper_index=5))


# ==== from tests/test-sec.py (prefix km_) ====
"""Automated unit test suite for MiOS KASLR Randomizer Manager."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "usr", "libexec", "mios", "sec"))

from kaslr_mgr import MIN_KASLR_ENTROPY_BITS, KASLRRandomizerManager

class km_TestKASLRMgr(unittest.TestCase):
    def setUp(self):
        self.mgr = KASLRRandomizerManager(dry_run=True)

    def test_kaslr_boot_address_randomization(self):
        """Test sampling kernel base address generates 2MB-aligned random offset."""
        sample = self.mgr.sample_boot_kernel_base(1)
        self.assertTrue(sample.text_base_address_hex.startswith("0xffffffff"))
        self.assertEqual(sample.offset_bytes % (2 * 1024 * 1024), 0)

    def test_15_reboots_yield_zero_duplicate_addresses_and_high_entropy(self):
        """Test 15 consecutive boot cycles yield zero duplicate base addresses and >28 bits entropy."""
        samples = [self.mgr.sample_boot_kernel_base(i) for i in range(15)]
        addresses = [s.text_base_address_hex for s in samples]
        self.assertEqual(len(set(addresses)), 15)  # 0 duplicates
        entropy = self.mgr.compute_address_variance_entropy(samples)
        self.assertGreaterEqual(entropy, MIN_KASLR_ENTROPY_BITS)


# ==== from tests/test-sec.py (prefix kl_) ====
"""Unit and integration test suite for LivepatchManager and livepatch_mgr CLI (task_490)."""


import importlib.util
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

kl__HERE = os.path.dirname(os.path.abspath(__file__))
kl__ROOT = os.path.normpath(os.path.join(kl__HERE, ".."))
kl__TARGET_PATH = os.path.join(kl__ROOT, "usr", "libexec", "mios", "sec", "livepatch_mgr.py")

kl_spec = importlib.util.spec_from_file_location("livepatch_mgr", kl__TARGET_PATH)
if kl_spec and kl_spec.loader:
    livepatch_mgr = importlib.util.module_from_spec(kl_spec)
    sys.modules[kl_spec.name] = livepatch_mgr
    kl_spec.loader.exec_module(livepatch_mgr)
else:
    raise ImportError(f"Could not load module from {kl__TARGET_PATH}")

class kl_TestKernelLivepatch(unittest.TestCase):
    """Test suite for LivepatchManager operations, MOK signature checks, and UKI staging."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory(prefix="mios-test-livepatch-")
        self.state_file = os.path.join(self.tmpdir.name, "livepatch-state.json")
        self.staging_dir = os.path.join(self.tmpdir.name, "uki-staging")
        self.sys_dir = os.path.join(self.tmpdir.name, "sys-livepatch")
        os.makedirs(self.sys_dir, exist_ok=True)
        os.makedirs(self.staging_dir, exist_ok=True)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_verify_mok_signature_mock_valid(self):
        mgr = livepatch_mgr.LivepatchManager(state_path=self.state_file, mock=True)
        sig = mgr.verify_mok_signature("/lib/modules/kpatch-cve-2026-1001.ko")
        self.assertTrue(sig["valid"])
        self.assertEqual(sig["signer"], "MiOS-MOK-CA-2026")
        self.assertIn("key_id", sig)

    def test_verify_mok_signature_mock_unsigned(self):
        mgr = livepatch_mgr.LivepatchManager(state_path=self.state_file, mock=True)
        sig = mgr.verify_mok_signature("/tmp/unsigned_hack_exploit.ko")
        self.assertFalse(sig["valid"])
        self.assertIn("Missing or untrusted", sig["reason"])

    def test_load_patch_success_mock(self):
        mgr = livepatch_mgr.LivepatchManager(state_path=self.state_file, mock=True)
        res = mgr.load_patch("/lib/modules/kpatch-cve-2026-1001.ko", "cve_2026_1001")
        self.assertTrue(res["success"])
        self.assertEqual(res["status"], "loaded")
        self.assertEqual(res["patch_name"], "cve_2026_1001")

        patches = mgr.list_patches()
        self.assertEqual(len(patches), 1)
        self.assertEqual(patches[0]["patch_name"], "cve_2026_1001")

    def test_load_patch_rejected_unsigned(self):
        mgr = livepatch_mgr.LivepatchManager(state_path=self.state_file, mock=True)
        res = mgr.load_patch("/tmp/unsigned_patch.ko")
        self.assertFalse(res["success"])
        self.assertEqual(res["status"], "signature_rejected")

    def test_unload_patch_mock(self):
        mgr = livepatch_mgr.LivepatchManager(state_path=self.state_file, mock=True)
        mgr.load_patch("/lib/modules/kpatch-fix.ko", "kpatch_fix")
        self.assertEqual(len(mgr.list_patches()), 1)

        res = mgr.unload_patch("kpatch_fix")
        self.assertTrue(res["success"])
        self.assertEqual(res["status"], "unloaded")
        self.assertEqual(len(mgr.list_patches()), 0)

    def test_reload_microcode_mock(self):
        mgr = livepatch_mgr.LivepatchManager(state_path=self.state_file, mock=True)
        res = mgr.reload_microcode()
        self.assertTrue(res["success"])
        self.assertEqual(res["status"], "microcode_reloaded")
        self.assertEqual(res["new_version"], "0x000000a2")
        self.assertIsNotNone(res["timestamp"])

    def test_stage_uki_update_mock(self):
        mgr = livepatch_mgr.LivepatchManager(
            state_path=self.state_file,
            staging_dir=self.staging_dir,
            mock=True,
        )
        res = mgr.stage_uki_update("/boot/efi/EFI/Linux/mios-6.10.uki")
        self.assertTrue(res["success"])
        self.assertEqual(res["status"], "uki_staged")
        self.assertIn("sha256", res["staged_uki"])

    def test_status_overview(self):
        mgr = livepatch_mgr.LivepatchManager(state_path=self.state_file, mock=True)
        mgr.load_patch("/lib/modules/kpatch-sec.ko", "sec_patch")
        status = mgr.get_status()
        self.assertEqual(status["active_patches_count"], 1)
        self.assertEqual(status["mok_enforcement"], "strict")
        self.assertEqual(status["uki_model"], "shim -> systemd-boot -> signed UKI")

    def test_file_signature_detection_real_bytes(self):
        mgr = livepatch_mgr.LivepatchManager(state_path=self.state_file, mock=False)
        test_ko = os.path.join(self.tmpdir.name, "signed_test.ko")
        with open(test_ko, "wb") as f:
            f.write(b"\x7fELF" + b"\x00" * 200 + b"~Module signature appended~" + b"\x00" * 40)

        sig = mgr.verify_mok_signature(test_ko)
        self.assertTrue(sig["valid"])
        self.assertIn("Module signature", sig["reason"])

    def test_main_cli_execution_mock(self):
        with patch.object(sys, "argv", ["livepatch_mgr.py", "--mock", "--status", "--json"]):
            code = livepatch_mgr.main()
            self.assertEqual(code, 0)

        with patch.object(sys, "argv", ["livepatch_mgr.py", "--mock", "--reload-microcode", "--json"]):
            code = livepatch_mgr.main()
            self.assertEqual(code, 0)


# ==== from tests/test-sec.py (prefix lp_) ====
"""Unit and integration test suite for LockdownProbe and CLI."""


import importlib.util
import json
import os
import sys
import unittest
from unittest.mock import patch

lp__HERE = os.path.dirname(os.path.abspath(__file__))
lp__ROOT = os.path.normpath(os.path.join(lp__HERE, ".."))
lp__TARGET_PATH = os.path.join(lp__ROOT, "usr", "libexec", "mios", "sec", "lockdown_probe.py")

lp_spec = importlib.util.spec_from_file_location("lockdown_probe", lp__TARGET_PATH)
if lp_spec and lp_spec.loader:
    lockdown_probe = importlib.util.module_from_spec(lp_spec)
    sys.modules[lp_spec.name] = lockdown_probe
    lp_spec.loader.exec_module(lockdown_probe)
else:
    raise ImportError(f"Could not load module from {lp__TARGET_PATH}")

class lp_TestLockdownProbe(unittest.TestCase):
    """Test suite for kernel lockdown modes, SecureBoot validation, and compliance evaluation."""

    def test_read_lockdown_mode_brackets(self):
        probe = lockdown_probe.LockdownProbe(mock=True)
        # Integrity mode
        mode_int = probe.read_lockdown_mode(mock_content="none [integrity] confidentiality")
        self.assertEqual(mode_int, "integrity")

        # Confidentiality mode
        mode_conf = probe.read_lockdown_mode(mock_content="none integrity [confidentiality]")
        self.assertEqual(mode_conf, "confidentiality")

        # None mode
        mode_none = probe.read_lockdown_mode(mock_content="[none] integrity confidentiality")
        self.assertEqual(mode_none, "none")

    def test_check_secureboot_and_module_signing_mock(self):
        probe = lockdown_probe.LockdownProbe(mock=True)
        self.assertTrue(probe.check_secureboot(mock_state=True))
        self.assertFalse(probe.check_secureboot(mock_state=False))
        self.assertTrue(probe.check_module_signing(mock_state=True))
        self.assertFalse(probe.check_module_signing(mock_state=False))

    def test_evaluate_lockdown_compliance_pass_and_fail(self):
        probe_integrity = lockdown_probe.LockdownProbe(mock=True, mock_mode="integrity")
        res_pass = probe_integrity.evaluate_lockdown_compliance(required_mode="integrity")
        self.assertEqual(res_pass["status"], "pass")
        self.assertTrue(res_pass["compliant"])
        self.assertEqual(res_pass["lockdown_mode"], "integrity")

        probe_none = lockdown_probe.LockdownProbe(mock=True, mock_mode="none")
        res_fail = probe_none.evaluate_lockdown_compliance(required_mode="integrity")
        self.assertEqual(res_fail["status"], "fail")
        self.assertFalse(res_fail["compliant"])
        self.assertEqual(res_fail["lockdown_mode"], "none")

    def test_cli_execution_probe_mock_integrity(self):
        test_args = [
            "lockdown_probe.py",
            "--probe",
            "--require-mode", "integrity",
            "--mock",
            "--mock-mode", "integrity",
            "--json",
        ]
        with patch.object(sys, "argv", test_args):
            exit_code = lockdown_probe.main()
            self.assertEqual(exit_code, 0)

    def test_cli_execution_probe_mock_none_exits_1(self):
        test_args = [
            "lockdown_probe.py",
            "--probe",
            "--require-mode", "integrity",
            "--mock",
            "--mock-mode", "none",
            "--json",
        ]
        with patch.object(sys, "argv", test_args):
            exit_code = lockdown_probe.main()
            self.assertEqual(exit_code, 1)

def lp_main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(lp_TestLockdownProbe)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# ==== from tests/test-sec.py (prefix lr_) ====
"""Automated tests for LUKS2 metadata parsing, header backup, atomic key rotation, and safety rollback."""


import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

lr__HERE = os.path.dirname(os.path.abspath(__file__))
lr__ROOT = os.path.normpath(os.path.join(lr__HERE, ".."))

sys.path.insert(0, os.path.join(lr__ROOT, "usr", "lib", "mios"))
sys.path.insert(0, os.path.join(lr__ROOT, "lib", "mios"))

lr__LUKS_PATH = os.path.join(lr__ROOT, "usr", "libexec", "mios", "sec", "mios-luks-rotate")
lr_loader = importlib.machinery.SourceFileLoader("luks_rotate", lr__LUKS_PATH)
lr_spec = importlib.util.spec_from_loader("luks_rotate", lr_loader)
if lr_spec and lr_spec.loader:
    luks_rotate = importlib.util.module_from_spec(lr_spec)
    sys.modules[lr_spec.name] = luks_rotate
    lr_spec.loader.exec_module(luks_rotate)
else:
    raise ImportError(f"Could not load mios-luks-rotate module from {lr__LUKS_PATH}")

class lr_MockLUKSDevice(luks_rotate.LUKSDevice):
    """Simulated LUKS device state machine for rigorous unit testing without physical disks."""

    def __init__(self, initial_slots: dict[int, str] | None = None) -> None:
        super().__init__()
        # Map slot_id -> passphrase
        self.slots: dict[int, str] = initial_slots if initial_slots is not None else {0: "initial-secret-passphrase"}
        self.header_backups: list[str] = []
        self.simulate_unlock_test_failure = False
        self.simulate_new_key_failure = False

    def dump_metadata(self, device: str) -> dict:
        active = sorted(list(self.slots.keys()))
        free = [i for i in range(32) if i not in self.slots]
        return {
            "device": device,
            "version": 2,
            "active_slots": active,
            "free_slots": free,
        }

    def backup_header(self, device: str, backup_path: str) -> str:
        os.makedirs(os.path.dirname(os.path.abspath(backup_path)), exist_ok=True)
        with open(backup_path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"device": device, "slots": self.slots}))
        self.header_backups.append(backup_path)
        return backup_path

    def add_key(self, device: str, current_passphrase: str, new_passphrase: str, new_slot: int) -> bool:
        if current_passphrase not in self.slots.values():
            raise RuntimeError("Current passphrase does not match any active keyslot")
        if new_slot in self.slots:
            raise RuntimeError(f"Keyslot {new_slot} is already occupied")
        self.slots[new_slot] = new_passphrase
        return True

    def test_passphrase(self, device: str, passphrase: str, slot: int | None = None) -> bool:
        if self.simulate_unlock_test_failure:
            return False
        if self.simulate_new_key_failure and (slot is not None and slot != 0):
            return False
        if slot is not None:
            return self.slots.get(slot) == passphrase
        return passphrase in self.slots.values()

    def kill_slot(self, device: str, slot_to_kill: int, active_passphrase: str | None = None) -> bool:
        if slot_to_kill not in self.slots:
            raise RuntimeError(f"Keyslot {slot_to_kill} does not exist")
        del self.slots[slot_to_kill]
        return True

    def restore_header(self, device: str, backup_path: str) -> bool:
        if not os.path.exists(backup_path):
            raise FileNotFoundError(backup_path)
        with open(backup_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            self.slots = {int(k): v for k, v in data["slots"].items()}
        return True

class lr_TestLUKSRotate(unittest.TestCase):
    """Tests LUKS2 metadata extraction, atomic zero-downtime key rotation, and safety rollback."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="mios_luks_test_")
        self.backup_dir = os.path.join(self.test_dir, "luks-headers")
        self.audit_log = os.path.join(self.test_dir, "luks-rotation.log")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_metadata_dump_active_and_free_slots(self):
        mock_dev = lr_MockLUKSDevice(initial_slots={0: "pass0", 1: "pass1"})
        meta = mock_dev.dump_metadata("/dev/mapper/ceph-osd-0")

        self.assertEqual(meta["active_slots"], [0, 1])
        self.assertEqual(meta["free_slots"][0], 2)
        self.assertEqual(len(meta["free_slots"]), 30)

    def test_successful_zero_downtime_key_rotation(self):
        mock_dev = lr_MockLUKSDevice(initial_slots={0: "old-passphrase-secret"})
        engine = luks_rotate.LUKSRotationEngine(
            luks_device=mock_dev,
            backup_root=self.backup_dir,
            audit_log=self.audit_log,
        )

        receipt = engine.rotate_key(
            device="/dev/mapper/ceph-osd-0",
            current_passphrase="old-passphrase-secret",
            new_passphrase="new-cryptographic-passphrase-2026",
        )

        self.assertEqual(receipt["status"], "success")
        self.assertEqual(receipt["old_slot"], 0)
        self.assertEqual(receipt["new_slot"], 1)
        self.assertTrue(receipt["post_verify_new_unlocks"])
        self.assertTrue(receipt["post_verify_old_revoked"])

        # Check mock device state: slot 0 should be gone, slot 1 should have new passphrase
        self.assertNotIn(0, mock_dev.slots)
        self.assertEqual(mock_dev.slots.get(1), "new-cryptographic-passphrase-2026")

        # Verify header backup file was created
        self.assertTrue(os.path.exists(receipt["backup_header"]))

        # Verify audit log was recorded
        self.assertTrue(os.path.exists(self.audit_log))
        with open(self.audit_log, "r", encoding="utf-8") as f:
            log_line = f.readline()
        log_data = json.loads(log_line)
        self.assertEqual(log_data["device"], "/dev/mapper/ceph-osd-0")
        self.assertEqual(log_data["old_slot"], 0)
        self.assertEqual(log_data["new_slot"], 1)

    def test_pre_validation_fails_on_wrong_current_passphrase(self):
        mock_dev = lr_MockLUKSDevice(initial_slots={0: "real-passphrase"})
        engine = luks_rotate.LUKSRotationEngine(
            luks_device=mock_dev,
            backup_root=self.backup_dir,
            audit_log=self.audit_log,
        )

        with self.assertRaises(ValueError) as ctx:
            engine.rotate_key(
                device="/dev/mapper/ceph-osd-0",
                current_passphrase="wrong-passphrase",
            )
        self.assertIn("Current passphrase validation failed", str(ctx.exception))
        # Initial slot untouched
        self.assertEqual(mock_dev.slots, {0: "real-passphrase"})

    def test_safety_invariant_new_key_failure_preserves_old_key(self):
        """CRITICAL: If testing new key fails, old keyslot MUST NOT be killed."""
        mock_dev = lr_MockLUKSDevice(initial_slots={0: "safe-old-passphrase"})
        engine = luks_rotate.LUKSRotationEngine(
            luks_device=mock_dev,
            backup_root=self.backup_dir,
            audit_log=self.audit_log,
        )

        # Simulate unlock verification failure on new slot
        mock_dev.simulate_new_key_failure = True

        with self.assertRaises(RuntimeError) as ctx:
            engine.rotate_key(
                device="/dev/mapper/ceph-osd-0",
                current_passphrase="safe-old-passphrase",
                new_passphrase="bad-new-passphrase",
            )

        self.assertIn("ABORTED ROTATION", str(ctx.exception))
        # Verify old keyslot was preserved!
        mock_dev.simulate_new_key_failure = False
        self.assertIn(0, mock_dev.slots)
        self.assertEqual(mock_dev.slots[0], "safe-old-passphrase")

    def test_header_backup_and_restore(self):
        mock_dev = lr_MockLUKSDevice(initial_slots={0: "original-key"})
        backup_file = os.path.join(self.backup_dir, "test.header.bak")

        mock_dev.backup_header("/dev/mapper/ceph-osd-0", backup_file)
        self.assertTrue(os.path.exists(backup_file))

        # Mutate device slots
        mock_dev.slots = {1: "mutated-key"}

        # Restore from backup
        mock_dev.restore_header("/dev/mapper/ceph-osd-0", backup_file)
        self.assertEqual(mock_dev.slots, {0: "original-key"})

    def test_service_and_timer_files_exist(self):
        svc_path = os.path.join(lr__ROOT, "usr", "lib", "systemd", "system", "mios-luks-rotate.service")
        timer_path = os.path.join(lr__ROOT, "usr", "lib", "systemd", "system", "mios-luks-rotate.timer")

        self.assertTrue(os.path.exists(svc_path), f"Service unit missing at {svc_path}")
        self.assertTrue(os.path.exists(timer_path), f"Timer unit missing at {timer_path}")

        with open(svc_path, "r", encoding="utf-8") as f:
            s_content = f.read()
        self.assertIn("mios-luks-rotate", s_content)

        with open(timer_path, "r", encoding="utf-8") as f:
            t_content = f.read()
        self.assertIn("OnCalendar=monthly", t_content)

def lr_main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(lr_TestLUKSRotate)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# ==== from tests/test-sec.py (prefix ns_) ====
"""Unit and integration test suite for NetSegmentationManager and CLI."""


import importlib.util
import json
import os
import sys
import unittest
from unittest.mock import patch

ns__HERE = os.path.dirname(os.path.abspath(__file__))
ns__ROOT = os.path.normpath(os.path.join(ns__HERE, ".."))
ns__TARGET_PATH = os.path.join(ns__ROOT, "usr", "libexec", "mios", "sec", "net_segmentation.py")
with open(os.path.join(ns__ROOT, "usr", "share", "mios", "mios.toml"), "rb") as _f:
    ns__PORTS = tomllib.load(_f)["ports"]  # expected ports come from SSOT, never a literal

ns_spec = importlib.util.spec_from_file_location("net_segmentation", ns__TARGET_PATH)
if ns_spec and ns_spec.loader:
    net_segmentation = importlib.util.module_from_spec(ns_spec)
    sys.modules[ns_spec.name] = net_segmentation
    ns_spec.loader.exec_module(net_segmentation)
else:
    raise ImportError(f"Could not load module from {ns__TARGET_PATH}")

def _ns_ssot_ports() -> dict:
    """[ports] from the SSOT, overridden by the resolved MIOS_PORT_* env."""
    import tomllib
    with open(os.path.join(ns__ROOT, "usr", "share", "mios", "mios.toml"), "rb") as fh:
        ports = tomllib.load(fh)["ports"]
    return {k: int(os.environ.get("MIOS_PORT_" + k.upper()) or ports[k])
            for k in ("hermes", "llm_light", "searxng")}

class ns_TestNetSegmentation(unittest.TestCase):
    """Test suite for nftables isolation ruleset generation, pairing matrix validation, and apply/flush."""

    def test_generate_nftables_rules_structure(self):
        mgr = net_segmentation.NetSegmentationManager(mock=True)
        rules = mgr.generate_nftables_rules(subnet="10.88.0.0/16")
        self.assertIn("table inet mios_isolation", rules)
        self.assertIn("chain forward_containers", rules)
        self.assertIn("policy drop", rules)
        ports = net_segmentation.mios_toml.vendor_tree(os.environ.get("MIOS_TOML_ROOT") or ns__ROOT)["ports"]
        for key in ("hermes", "pgvector", "llm_light", "searxng"):
            self.assertIn(f"dport {ports[key]} accept", rules, key)
        accepts = [ln for ln in rules.splitlines() if "dport" in ln]
        self.assertEqual(len(accepts), len(set(accepts)), "duplicate accept line")
        self.assertIn("log prefix \"MIOS-NET-DROP: \"", rules)

    def test_validate_pairing_matrix_valid_default(self):
        mgr = net_segmentation.NetSegmentationManager(mock=True)
        valid, violations = mgr.validate_pairing_matrix(mgr.DEFAULT_ALLOWED_PAIRINGS)
        self.assertTrue(valid)
        self.assertEqual(len(violations), 0)

    def test_validate_pairing_matrix_rejects_forbidden_pairings(self):
        mgr = net_segmentation.NetSegmentationManager(mock=True)
        bad_pairings = [
            {"src": "open-webui", "dst": "pgvector", "port": 5432},
            {"src": "open-webui", "dst": "llm-heavy", "port": 11441},
        ]
        valid, violations = mgr.validate_pairing_matrix(bad_pairings)
        self.assertFalse(valid)
        self.assertGreaterEqual(len(violations), 2)
        self.assertTrue(any("Direct UI-to-DB" in v for v in violations))

    def test_the_database_port_guard_follows_the_ssot_port(self):
        mgr = net_segmentation.NetSegmentationManager(mock=True)
        db_port = int(net_segmentation.mios_toml.section(net_segmentation.mios_toml.load_merged(), "ports")["pgvector"])
        valid, violations = mgr.validate_pairing_matrix([{"src": "open-webui", "dst": "db-proxy", "port": db_port}])
        self.assertFalse(valid, "a pairing onto [ports].pgvector under another name must still be guarded")
        self.assertTrue(any("database port %d" % db_port in v for v in violations), violations)

    def test_apply_and_flush_rules_mock(self):
        mgr = net_segmentation.NetSegmentationManager(mock=True)
        rules = mgr.generate_nftables_rules()
        self.assertTrue(mgr.apply_rules(rules))
        self.assertTrue(mgr.flush_isolation())

    def test_cli_execution_generate(self):
        test_args = [
            "net_segmentation.py",
            "--generate",
            "--subnet", "10.88.0.0/16",
            "--mock",
            "--json",
        ]
        with patch.object(sys, "argv", test_args):
            exit_code = net_segmentation.main()
            self.assertEqual(exit_code, 0)

    def test_cli_execution_validate_matrix(self):
        test_args = [
            "net_segmentation.py",
            "--validate-matrix",
            "--mock",
            "--json",
        ]
        with patch.object(sys, "argv", test_args):
            exit_code = net_segmentation.main()
            self.assertEqual(exit_code, 0)

    def test_cli_execution_apply(self):
        test_args = [
            "net_segmentation.py",
            "--apply",
            "--mock",
            "--json",
        ]
        with patch.object(sys, "argv", test_args):
            exit_code = net_segmentation.main()
            self.assertEqual(exit_code, 0)

def ns_main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(ns_TestNetSegmentation)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# ==== from tests/test-sec.py (prefix pr_) ====
"""Unit and integration test suite for PanicRollbackHandler and CLI."""


import importlib.util
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

pr__HERE = os.path.dirname(os.path.abspath(__file__))
pr__ROOT = os.path.normpath(os.path.join(pr__HERE, ".."))
pr__TARGET_PATH = os.path.join(pr__ROOT, "usr", "libexec", "mios", "sec", "panic_rollback.py")

pr_spec = importlib.util.spec_from_file_location("panic_rollback", pr__TARGET_PATH)
if pr_spec and pr_spec.loader:
    panic_rollback = importlib.util.module_from_spec(pr_spec)
    sys.modules[pr_spec.name] = panic_rollback
    pr_spec.loader.exec_module(panic_rollback)
else:
    raise ImportError(f"Could not load module from {pr__TARGET_PATH}")

class pr_TestPanicRollback(unittest.TestCase):
    """Test suite for pstore crash dump scanning, boot failure persistence, and rollback triggering."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="mios-test-panic-")
        self.state_file = os.path.join(self.temp_dir.name, "boot_fails.json")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_scan_pstore_mock_with_panic(self):
        handler = panic_rollback.PanicRollbackHandler(mock=True, mock_failures=1)
        panics = handler.scan_pstore()
        self.assertEqual(len(panics), 1)
        self.assertIn("kernel panic", panics[0]["reason"].lower())

    def test_record_failure_and_persistence(self):
        handler = panic_rollback.PanicRollbackHandler(mock=True)
        count1 = handler.record_failure(reason="panic_1", state_file=self.state_file)
        self.assertEqual(count1, 1)

        count2 = handler.record_failure(reason="panic_2", state_file=self.state_file)
        self.assertEqual(count2, 2)

        state = handler.read_state(self.state_file)
        self.assertEqual(state["failure_count"], 2)
        self.assertEqual(len(state["history"]), 2)

    def test_reset_counter(self):
        handler = panic_rollback.PanicRollbackHandler(mock=True)
        handler.record_failure(reason="panic_1", state_file=self.state_file)
        self.assertTrue(handler.reset_counter(state_file=self.state_file))

        state = handler.read_state(self.state_file)
        self.assertEqual(state["failure_count"], 0)

    def test_evaluate_rollback_threshold(self):
        # Under threshold (2 < 3) -> no rollback
        h_healthy = panic_rollback.PanicRollbackHandler(mock=True, mock_failures=2)
        res_healthy = h_healthy.evaluate_rollback(max_failures=3, state_file=self.state_file)
        self.assertEqual(res_healthy["status"], "healthy")
        self.assertFalse(res_healthy["rollback_executed"])

        # At/above threshold (3 >= 3) -> rollback triggered
        h_panic = panic_rollback.PanicRollbackHandler(mock=True, mock_failures=3)
        res_rollback = h_panic.evaluate_rollback(max_failures=3, state_file=self.state_file)
        self.assertEqual(res_rollback["status"], "rollback_triggered")
        self.assertEqual(res_rollback["action"], "bootc_rollback")
        self.assertTrue(res_rollback["rollback_executed"])

    def test_cli_execution_record_failure(self):
        test_args = [
            "panic_rollback.py",
            "--record-failure",
            "--state-file", self.state_file,
            "--mock",
            "--json",
        ]
        with patch.object(sys, "argv", test_args):
            exit_code = panic_rollback.main()
            self.assertEqual(exit_code, 0)

    def test_cli_execution_reset_counter(self):
        test_args = [
            "panic_rollback.py",
            "--reset-counter",
            "--state-file", self.state_file,
            "--mock",
            "--json",
        ]
        with patch.object(sys, "argv", test_args):
            exit_code = panic_rollback.main()
            self.assertEqual(exit_code, 0)

    def test_cli_execution_evaluate_rollback(self):
        test_args = [
            "panic_rollback.py",
            "--evaluate-rollback",
            "--max-failures", "3",
            "--mock",
            "--mock-failures", "3",
            "--json",
        ]
        with patch.object(sys, "argv", test_args):
            exit_code = panic_rollback.main()
            self.assertEqual(exit_code, 0)

def pr_main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(pr_TestPanicRollback)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# ==== from tests/test-sec.py (prefix qsr_) ====
"""Automated tests for WS-SEC Quadlet secret file permissions audit and token rotation."""


import importlib.util
import os
import stat
import sys
import tempfile
import unittest

qsr__HERE = os.path.dirname(os.path.abspath(__file__))
qsr__ROOT = os.path.normpath(os.path.join(qsr__HERE, ".."))
qsr__ROT_PATH = os.path.join(qsr__ROOT, "usr", "libexec", "mios", "sec", "rotate-quadlet-secrets.py")

qsr_spec = importlib.util.spec_from_file_location("rotate_quadlet_secrets", qsr__ROT_PATH)
if qsr_spec and qsr_spec.loader:
    rotate_quadlet_secrets = importlib.util.module_from_spec(qsr_spec)
    sys.modules[qsr_spec.name] = rotate_quadlet_secrets
    qsr_spec.loader.exec_module(rotate_quadlet_secrets)
else:
    raise ImportError(f"Could not load rotate-quadlet-secrets module from {qsr__ROT_PATH}")

class qsr_TestQuadletSecretsRotation(unittest.TestCase):
    """Validates 0600 permission hardening on env files and secure token rotation generation."""

    def test_permission_hardening(self):
        with tempfile.TemporaryDirectory(prefix="mios-sec-test-") as tmpdir:
            test_env = os.path.join(tmpdir, "test.env")
            with open(test_env, "w", encoding="utf-8") as f:
                f.write("API_KEY=12345\n")
            os.chmod(test_env, 0o644)

            hardener = rotate_quadlet_secrets.QuadletSecretsHardener(secrets_dir=tmpdir)
            fixed = hardener.audit_and_harden_permissions(tmpdir)
            self.assertEqual(len(fixed), 1)
            if os.name != "nt":
                st = os.stat(test_env)
                self.assertEqual(stat.S_IMODE(st.st_mode), 0o600)

    def test_token_rotation_generation(self):
        hardener = rotate_quadlet_secrets.QuadletSecretsHardener()
        token, line = hardener.generate_rotated_secret("AGENT_AUTH_TOKEN", length_bytes=32)
        self.assertEqual(len(token), 64)
        self.assertTrue(line.startswith("AGENT_AUTH_TOKEN="))

    def test_init_secrets_env_non_destructive(self):
        with tempfile.TemporaryDirectory(prefix="mios-sec-init-") as tmpdir:
            sec_file = os.path.join(tmpdir, "secrets.env")
            with open(sec_file, "w", encoding="utf-8") as f:
                f.write("POSTGRES_PASSWORD=my_existing_db_password_123\n")

            hardener = rotate_quadlet_secrets.QuadletSecretsHardener(secrets_dir=tmpdir)
            secrets_map = hardener.init_secrets_env(secrets_file=sec_file)

            # Assert pre-existing password is NOT disrupted
            self.assertEqual(secrets_map["POSTGRES_PASSWORD"], "my_existing_db_password_123")
            # Assert missing default keys were generated
            self.assertIn("MIOS_IDENTITY_DEFAULT_PASSWORD", secrets_map)
            self.assertIn("K3S_TOKEN", secrets_map)
            self.assertIn("WEBUI_SECRET_KEY", secrets_map)
            self.assertEqual(len(secrets_map["K3S_TOKEN"]), 64)

            # Re-read file from disk
            with open(sec_file, "r", encoding="utf-8") as f:
                content = f.read()
            self.assertIn("POSTGRES_PASSWORD=my_existing_db_password_123", content)
            self.assertIn("K3S_TOKEN=", content)

    def test_service_unit_file(self):
        svc_path = os.path.join(qsr__ROOT, "usr", "lib", "systemd", "system", "mios-secret-init.service")
        self.assertTrue(os.path.exists(svc_path), f"Service file missing at {svc_path}")
        with open(svc_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("rotate-quadlet-secrets.py --init", content)
        self.assertIn("[Install]", content)

    def test_unreadable_existing_file_never_regenerates(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "secrets.env")
            original = b"POSTGRES_PASSWORD=preserved-test-credential\n"
            with open(path, "wb") as stream:
                stream.write(original)
            failure = PermissionError("injected existing-file read denial")
            with patch("builtins.open", side_effect=failure), patch.object(os, "open", side_effect=failure):
                with self.assertRaises(PermissionError):
                    rotate_quadlet_secrets.init_secrets_env(path)
            with open(path, "rb") as stream:
                self.assertEqual(stream.read(), original)
            self.assertEqual(os.listdir(folder), ["secrets.env"])

    def test_failed_publication_preserves_existing_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "secrets.env")
            original = b"POSTGRES_PASSWORD=preserved-test-credential\n"
            with open(path, "wb") as stream:
                stream.write(original)
            real_open = open
            def fail_direct_write(filename, mode="r", *args, **kwargs):
                if "w" in mode:
                    raise OSError("injected direct-write failure")
                return real_open(filename, mode, *args, **kwargs)
            with patch("builtins.open", side_effect=fail_direct_write), patch.object(os, "replace", side_effect=OSError("injected publication failure")):
                with self.assertRaises(OSError):
                    rotate_quadlet_secrets.init_secrets_env(path)
            with open(path, "rb") as stream:
                self.assertEqual(stream.read(), original)
            self.assertEqual(os.listdir(folder), ["secrets.env"])

    def test_private_staging_precedes_first_secret_write(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "secrets.env")
            observations = []
            real_open, real_fdopen = open, os.fdopen
            class ObservedWriter:
                def __init__(self, stream):
                    self.stream = stream
                def __enter__(self):
                    self.stream.__enter__()
                    return self
                def __exit__(self, *args):
                    return self.stream.__exit__(*args)
                def __getattr__(self, name):
                    return getattr(self.stream, name)
                def write(self, value):
                    observations.append((os.path.exists(path), stat.S_IMODE(os.fstat(self.stream.fileno()).st_mode)))
                    return self.stream.write(value)
            def observed_open(filename, mode="r", *args, **kwargs):
                stream = real_open(filename, mode, *args, **kwargs)
                return ObservedWriter(stream) if "w" in mode else stream
            def observed_fdopen(fd, mode="r", *args, **kwargs):
                stream = real_fdopen(fd, mode, *args, **kwargs)
                return ObservedWriter(stream) if "w" in mode else stream
            with patch("builtins.open", side_effect=observed_open), patch.object(os, "fdopen", side_effect=observed_fdopen):
                values = rotate_quadlet_secrets.init_secrets_env(path)
            self.assertTrue(observations, "the production writer must write data")
            self.assertTrue(all(not visible for visible, _ in observations))
            if os.name != "nt":
                self.assertTrue(all(mode == 0o600 for _, mode in observations))
                self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            self.assertEqual(os.listdir(folder), ["secrets.env"])
            with open(path, encoding="utf-8") as stream:
                content = stream.read()
            self.assertTrue(all(f"{key}={value}\n" in content for key, value in values.items()))

    def test_permission_failure_is_not_success(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "secrets.env")
            with patch.object(os, "chmod", side_effect=PermissionError("injected private-mode failure")):
                with self.assertRaises(PermissionError):
                    rotate_quadlet_secrets.init_secrets_env(path)
            self.assertEqual(os.listdir(folder), [])

    @unittest.skipIf(os.name == "nt", "POSIX symlink controls run on Linux; Windows ACL certification remains separate")
    def test_symlink_target_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            victim = os.path.join(folder, "operator.env")
            path = os.path.join(folder, "secrets.env")
            original = b"POSTGRES_PASSWORD=preserved-test-credential\n"
            with open(victim, "wb") as stream:
                stream.write(original)
            os.symlink(victim, path)
            with self.assertRaises(OSError):
                rotate_quadlet_secrets.init_secrets_env(path)
            self.assertTrue(os.path.islink(path))
            with open(victim, "rb") as stream:
                self.assertEqual(stream.read(), original)

def qsr_main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(qsr_TestQuadletSecretsRotation)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# ==== from tests/test-sec.py (prefix ra_) ====
"""Unit and integration test suite for RemoteAttestationEngine and CLI."""


import importlib.util
import json
import os
import sys
import unittest
from unittest.mock import patch

ra__HERE = os.path.dirname(os.path.abspath(__file__))
ra__ROOT = os.path.normpath(os.path.join(ra__HERE, ".."))
ra__TARGET_PATH = os.path.join(ra__ROOT, "usr", "libexec", "mios", "sec", "remote_attestation.py")

ra_spec = importlib.util.spec_from_file_location("remote_attestation", ra__TARGET_PATH)
if ra_spec and ra_spec.loader:
    remote_attestation = importlib.util.module_from_spec(ra_spec)
    sys.modules[ra_spec.name] = remote_attestation
    ra_spec.loader.exec_module(remote_attestation)
else:
    raise ImportError(f"Could not load module from {ra__TARGET_PATH}")

class ra_TestRemoteAttestation(unittest.TestCase):
    """Test suite for TPM2 quotes, nonces, report generation, and peer measurement verification."""

    def test_generate_tpm2_quote_mock(self):
        engine = remote_attestation.RemoteAttestationEngine(mock=True)
        quote = engine.generate_tpm2_quote(pcr_list=[0, 7, 11, 14], nonce="aabbccddeeff0011")
        self.assertEqual(quote["pcr_list"], [0, 7, 11, 14])
        self.assertEqual(quote["nonce"], "aabbccddeeff0011")
        self.assertEqual(len(quote["pcrs"]), 4)
        self.assertIn("pcr_digest", quote)
        self.assertIn("quote_signature", quote)

    def test_build_report_schema(self):
        engine = remote_attestation.RemoteAttestationEngine(mock=True)
        rep = engine.build_report(node_id="test-node-01", pcr_list=[0, 7, 11, 14], nonce="1234567890abcdef")
        self.assertEqual(rep["version"], "1.0")
        self.assertEqual(rep["node_id"], "test-node-01")
        self.assertIn("quote", rep)
        self.assertIn("kernel_release", rep)
        self.assertIn("uki_hash", rep)

    def test_verify_report_matching_golden_pcrs(self):
        engine = remote_attestation.RemoteAttestationEngine(mock=True)
        rep = engine.build_report(node_id="node-a", pcr_list=[0, 7, 11, 14], nonce="nonce-123")

        golden = rep["quote"]["pcrs"]
        res = engine.verify_report(report=rep, golden_pcrs=golden, expected_nonce="nonce-123")
        self.assertTrue(res["valid"])
        self.assertEqual(res["status"], "verified")
        self.assertTrue(res["nonce_valid"])
        self.assertTrue(res["pcr_measurements_valid"])

    def test_verify_report_nonce_mismatch_rejected(self):
        engine = remote_attestation.RemoteAttestationEngine(mock=True)
        rep = engine.build_report(node_id="node-a", nonce="nonce-AAA")

        res = engine.verify_report(report=rep, expected_nonce="nonce-BBB")
        self.assertFalse(res["valid"])
        self.assertEqual(res["status"], "rejected")
        self.assertIn("Nonce challenge mismatch", res["error"])

    def test_verify_report_pcr_mismatch_rejected(self):
        engine = remote_attestation.RemoteAttestationEngine(mock=True)
        rep = engine.build_report(node_id="node-a", nonce="nonce-123")

        # Golden expectation differing from quote
        golden = dict(rep["quote"]["pcrs"])
        golden["7"] = "0000000000000000000000000000000000000000000000000000000000000000"

        res = engine.verify_report(report=rep, golden_pcrs=golden, expected_nonce="nonce-123")
        self.assertFalse(res["valid"])
        self.assertEqual(res["status"], "rejected")
        self.assertIn("PCR baseline mismatch", res["error"])

    def test_cli_execution_generate_quote(self):
        test_args = [
            "remote_attestation.py",
            "--generate-quote",
            "--pcr-list", "0,7,11,14",
            "--nonce", "deadbeef12345678",
            "--mock",
            "--json",
        ]
        with patch.object(sys, "argv", test_args):
            exit_code = remote_attestation.main()
            self.assertEqual(exit_code, 0)

    def test_cli_execution_verify_quote(self):
        test_args = [
            "remote_attestation.py",
            "--verify-quote",
            "--node-id", "test-node-99",
            "--mock",
            "--json",
        ]
        with patch.object(sys, "argv", test_args):
            exit_code = remote_attestation.main()
            self.assertEqual(exit_code, 0)

def ra_main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(ra_TestRemoteAttestation)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# ==== from tests/test-sec.py (prefix sg_) ====
"""Automated unit test suite for MiOS SBOM Generator."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "usr", "libexec", "mios", "sec"))

from sbom_gen import SBOMGenerator

class sg_TestSBOMGen(unittest.TestCase):
    def setUp(self):
        self.gen = SBOMGenerator(dry_run=True)

    def test_sbom_generation_with_100_percent_package_inventory(self):
        """Test SBOM includes all scanned packages and generates valid Cosign signature."""
        pkgs = [f"rpm_pkg_{i}" for i in range(50)] + [f"wheel_{i}" for i in range(20)]
        res = self.gen.generate_image_sbom(pkgs)
        self.assertEqual(res.total_packages_scanned, 70)
        self.assertTrue(res.is_signature_valid)
        self.assertTrue(res.cosign_attestation_signature.startswith("cosign_sig_"))


# ==== from tests/test-sec.py (prefix sm_) ====
"""Automated unit test suite for MiOS Secure In-Memory Secret Enclave."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "usr", "libexec", "mios", "sec"))

from secret_mem import SecretBuffer, SecretEnclave

class sm_TestSecretMem(unittest.TestCase):
    def test_context_manager_lifecycle(self):
        """Test that SecretBuffer holds secret within context and wipes on exit."""
        secret_text = "sk_live_super_secret_token_123456789"
        buf_ref = None

        with SecretEnclave.hold(secret_text) as buf:
            buf_ref = buf
            self.assertFalse(buf.is_wiped)
            self.assertEqual(buf.get_bytes().decode("utf-8"), secret_text)

        # After exiting context, buffer must be wiped
        self.assertTrue(buf_ref.is_wiped)
        with self.assertRaises(ValueError):
            buf_ref.get_bytes()

    def test_direct_memory_zeroization(self):
        """Test that underlying memory bytes are strictly overwritten with 0x00."""
        secret_bytes = b"deterministic_zeroization_test_bytes"
        buf = SecretBuffer(secret_bytes)
        self.assertEqual(buf.get_bytes(), secret_bytes)

        # Trigger manual wipe
        buf.wipe()
        self.assertTrue(buf.is_wiped)

        # Inspect internal C buffer directly
        raw_bytes = bytes(buf._c_buf)
        self.assertEqual(raw_bytes, b"\x00" * len(secret_bytes))

    def test_repr_and_str_redaction(self):
        """Test that secret plaintext never leaks into __str__ or __repr__."""
        sensitive_key = "PRIVATE_KEY_DO_NOT_LEAK_IN_LOGS"
        buf = SecretBuffer(sensitive_key)

        str_rep = str(buf)
        repr_rep = repr(buf)

        self.assertNotIn(sensitive_key, str_rep)
        self.assertNotIn(sensitive_key, repr_rep)
        self.assertIn("REDACTED", str_rep)
        buf.wipe()

    def test_core_dump_leak_verification(self):
        """Test simulation of core dump memory search asserting 0 plaintext occurrences."""
        secret_token = "enclave_isolated_api_bearer_9999"
        buf = SecretBuffer(secret_token)

        # Simulate process memory containing buffer contents
        active_memory = b"process_header_data..." + buf.get_bytes() + b"...process_footer"
        self.assertFalse(SecretEnclave.verify_no_core_leak(secret_token, active_memory))

        # Wipe buffer
        buf.wipe()
        wiped_memory = b"process_header_data..." + bytes(buf._c_buf) + b"...process_footer"
        self.assertTrue(SecretEnclave.verify_no_core_leak(secret_token, wiped_memory))

    def test_enclave_status_flags(self):
        """Test reporting of enclave capabilities and kernel security flags."""
        status = SecretEnclave.get_enclave_status()
        self.assertTrue(status["enclave_ready"])
        self.assertIn("madv_dontdump_flag", status)
        self.assertIn("madv_wipeonfork_flag", status)


# ==== from tests/test-sec.py (prefix sp_) ====
"""Unit and integration test suite for SelinuxPolicyManager and CLI."""


import importlib.util
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sp__HERE = os.path.dirname(os.path.abspath(__file__))
sp__ROOT = os.path.normpath(os.path.join(sp__HERE, ".."))
sp__TARGET_PATH = os.path.join(sp__ROOT, "usr", "libexec", "mios", "sec", "selinux_policy.py")

sp_spec = importlib.util.spec_from_file_location("selinux_policy", sp__TARGET_PATH)
if sp_spec and sp_spec.loader:
    selinux_policy = importlib.util.module_from_spec(sp_spec)
    sys.modules[sp_spec.name] = selinux_policy
    sp_spec.loader.exec_module(selinux_policy)
else:
    raise ImportError(f"Could not load module from {sp__TARGET_PATH}")

class sp_TestSelinuxPolicy(unittest.TestCase):
    """Test suite for SELinux Type Enforcement (.te) generation, mock compilation, and AVC denial parsing."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="mios-test-selinux-")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_generate_te_source_syntax(self):
        manager = selinux_policy.SelinuxPolicyManager(mock=True)
        te_src = manager.generate_te_source(
            module_name="mios_sidecar",
            allowed_ports=[5432, 8600, 8720],
            allowed_dirs=["/var/lib/mios"],
        )
        self.assertIn("module mios_sidecar 1.0;", te_src)
        self.assertIn("type mios_sidecar_t;", te_src)
        self.assertIn("typeattribute mios_sidecar_t container_domain;", te_src)
        self.assertIn("5432, 8600, 8720", te_src)

    def test_compile_module_mock(self):
        manager = selinux_policy.SelinuxPolicyManager(mock=True)
        te_path = os.path.join(self.temp_dir.name, "mios_sidecar.te")
        with open(te_path, "w", encoding="utf-8") as f:
            f.write("module mios_sidecar 1.0;\n")

        res = manager.compile_module(te_path)
        self.assertTrue(os.path.exists(res["mod_file"]))
        self.assertTrue(os.path.exists(res["pp_file"]))

    def test_install_module_mock(self):
        manager = selinux_policy.SelinuxPolicyManager(mock=True)
        pp_path = os.path.join(self.temp_dir.name, "mios_sidecar.pp")
        self.assertTrue(manager.install_module(pp_path))

    def test_parse_avc_denials_matching_domain(self):
        manager = selinux_policy.SelinuxPolicyManager(mock=True)
        sample_log = (
            'type=AVC msg=audit(1724670000.123:456): avc:  denied  { read } for  '
            'pid=1234 comm="hermes" name="unauthorized.txt" dev="dm-0" ino=5678 '
            'scontext=system_u:system_r:mios_sidecar_t:s0:c123,c456 '
            'tcontext=system_u:object_r:admin_home_t:s0 tclass=file permissive=0\n'
            'type=AVC msg=audit(1724670005.123:457): avc:  denied  { write } for  '
            'pid=5678 comm="unrelated" name="file.txt" '
            'scontext=system_u:system_r:unconfined_t:s0 '
            'tcontext=system_u:object_r:etc_t:s0 tclass=file permissive=0\n'
        )

        denials = manager.parse_avc_denials(sample_log, target_domain="mios_sidecar_t")
        self.assertEqual(len(denials), 1)
        self.assertEqual(denials[0]["audit_id"], "456")
        self.assertIn("read", denials[0]["permissions"])
        self.assertIn("mios_sidecar_t", denials[0]["scontext"])

    def test_audit_sidecar_confinement_mock(self):
        manager = selinux_policy.SelinuxPolicyManager(mock=True)
        audit_res = manager.audit_sidecar_confinement()
        self.assertTrue(audit_res["enforcing"])
        self.assertTrue(audit_res["compliant"])
        self.assertEqual(len(audit_res["unconfined_containers"]), 0)

    def test_cli_execution_generate_te(self):
        te_file = os.path.join(self.temp_dir.name, "custom.te")
        test_args = [
            "selinux_policy.py",
            "--generate-te",
            "--module-name", "custom_test",
            "--te-file", te_file,
            "--mock",
            "--json",
        ]
        with patch.object(sys, "argv", test_args):
            exit_code = selinux_policy.main()
            self.assertEqual(exit_code, 0)
            self.assertTrue(os.path.exists(te_file))

    def test_cli_execution_status(self):
        test_args = [
            "selinux_policy.py",
            "--status",
            "--mock",
            "--json",
        ]
        with patch.object(sys, "argv", test_args):
            exit_code = selinux_policy.main()
            self.assertEqual(exit_code, 0)

def sp_main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(sp_TestSelinuxPolicy)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# ==== from tests/test-sec.py (prefix slp_) ====
"""Unit and integration test suite for SlsaProvenanceEngine and CLI."""


import importlib.util
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

slp__HERE = os.path.dirname(os.path.abspath(__file__))
slp__ROOT = os.path.normpath(os.path.join(slp__HERE, ".."))
slp__TARGET_PATH = os.path.join(slp__ROOT, "usr", "libexec", "mios", "sec", "slsa_provenance.py")

slp_spec = importlib.util.spec_from_file_location("slsa_provenance", slp__TARGET_PATH)
if slp_spec and slp_spec.loader:
    slsa_provenance = importlib.util.module_from_spec(slp_spec)
    sys.modules[slp_spec.name] = slsa_provenance
    slp_spec.loader.exec_module(slsa_provenance)
else:
    raise ImportError(f"Could not load module from {slp__TARGET_PATH}")

class slp_TestSlsaProvenance(unittest.TestCase):
    """Test suite for SLSA v1 provenance statements, artifact hashing, and DSSE envelope verification."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="mios-test-slsa-")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_compute_artifact_hash_real_file(self):
        test_file = os.path.join(self.temp_dir.name, "sample_build.raw")
        with open(test_file, "wb") as f:
            f.write(b"SAMPLE-BUILD-ARTIFACT-DATA-12345")

        engine = slsa_provenance.SlsaProvenanceEngine(mock=False)
        digest = engine.compute_artifact_hash(test_file)
        self.assertEqual(len(digest), 64)

    def test_generate_statement_schema_compliance(self):
        engine = slsa_provenance.SlsaProvenanceEngine(mock=True)
        stmt = engine.generate_statement(
            artifact_path="build/mios-bootc.raw",
            builder_id="https://github.com/mios-dev/mios/.github/workflows/build-bootc.yml@refs/heads/main",
            source_repo="https://github.com/mios-dev/mios",
            commit_sha="a1b2c3d4e5f60718293a4b5c6d7e8f9012345678",
        )

        self.assertEqual(stmt["_type"], "https://in-toto.io/Statement/v1")
        self.assertEqual(stmt["predicateType"], "https://slsa.dev/provenance/v1")
        self.assertEqual(len(stmt["subject"]), 1)
        self.assertEqual(stmt["subject"][0]["name"], "mios-bootc.raw")
        self.assertIn("sha256", stmt["subject"][0]["digest"])

        pred = stmt["predicate"]
        self.assertEqual(pred["buildDefinition"]["buildType"], "https://mios.dev/builds/bootc-bib/v1")
        self.assertEqual(pred["runDetails"]["builder"]["id"], "https://github.com/mios-dev/mios/.github/workflows/build-bootc.yml@refs/heads/main")

    def test_verify_statement_matching_and_mismatching_artifacts(self):
        test_file = os.path.join(self.temp_dir.name, "target_image.img")
        with open(test_file, "wb") as f:
            f.write(b"IMAGE-CONTENT-AAA")

        engine = slsa_provenance.SlsaProvenanceEngine(mock=False)
        stmt = engine.generate_statement(
            artifact_path=test_file,
            builder_id="https://github.com/mios-dev/mios/builder",
            source_repo="https://github.com/mios-dev/mios",
            commit_sha="1111222233334444555566667777888899990000",
        )

        # 1. Match verification
        ver_ok = engine.verify_statement(
            statement=stmt,
            artifact_path=test_file,
            expected_builder="https://github.com/mios-dev/mios/builder",
            expected_source="https://github.com/mios-dev/mios",
        )
        self.assertTrue(ver_ok["valid"])
        self.assertTrue(ver_ok["digest_matched"])

        # 2. Tampered file mismatch
        tampered_file = os.path.join(self.temp_dir.name, "tampered.img")
        with open(tampered_file, "wb") as f:
            f.write(b"TAMPERED-CONTENT-BBB")

        ver_tampered = engine.verify_statement(
            statement=stmt,
            artifact_path=tampered_file,
        )
        self.assertFalse(ver_tampered["valid"])
        self.assertIn("mismatch", ver_tampered["error"])

    def test_sign_and_verify_dsse_envelope(self):
        engine = slsa_provenance.SlsaProvenanceEngine(mock=True)
        stmt = engine.generate_statement("build/mios.raw")
        envelope = engine.sign_envelope(stmt)

        self.assertEqual(envelope["payloadType"], "application/vnd.in-toto+json")
        self.assertIn("payload", envelope)
        self.assertEqual(len(envelope["signatures"]), 1)

        # Verify envelope
        self.assertTrue(engine.verify_envelope(envelope))

    def test_cli_execution_generate(self):
        test_args = [
            "slsa_provenance.py",
            "--generate",
            "--artifact", "build/mios-bootc.raw",
            "--sign",
            "--mock",
            "--json",
        ]
        with patch.object(sys, "argv", test_args):
            exit_code = slsa_provenance.main()
            self.assertEqual(exit_code, 0)

    def test_cli_execution_verify(self):
        test_args = [
            "slsa_provenance.py",
            "--verify",
            "--artifact", "build/mios-bootc.raw",
            "--mock",
            "--json",
        ]
        with patch.object(sys, "argv", test_args):
            exit_code = slsa_provenance.main()
            self.assertEqual(exit_code, 0)

def slp_main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(slp_TestSlsaProvenance)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# ==== from tests/test-sec.py (prefix scm_) ====
"""Automated unit test suite for MiOS Virtual CCID Multiplexer."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "usr", "libexec", "mios", "sec"))

from smartcard_mux import VirtualCCIDMultiplexer

class scm_TestSmartcardMux(unittest.TestCase):
    def setUp(self):
        self.mux = VirtualCCIDMultiplexer(dry_run=True)

    def test_single_tenant_commit_signing(self):
        """Test virtual CCID executes cryptographic commit signing."""
        res = self.mux.execute_signing_request("tenant_alpha", "tree_sha_123456")
        self.assertTrue(res.is_success)
        self.assertIn("sig_", res.signature_hex)

    def test_10_concurrent_signing_requests_zero_collisions(self):
        """Test 10 concurrent tenants complete signing without key collisions."""
        signatures = set()
        for i in range(10):
            res = self.mux.execute_signing_request(f"tenant_{i}", f"payload_{i}")
            self.assertTrue(res.is_success)
            signatures.add(res.signature_hex)
        self.assertEqual(len(signatures), 10)


# ==== from tests/test-sec.py (prefix smt_) ====
"""Unit and integration test suite for SpiffeIdentityAgent and spiffe_identity CLI (task_512)."""


import datetime
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

smt__HERE = os.path.dirname(os.path.abspath(__file__))
smt__ROOT = os.path.normpath(os.path.join(smt__HERE, ".."))
smt__TARGET_PATH = os.path.join(smt__ROOT, "usr", "libexec", "mios", "sec", "spiffe_identity.py")

smt_spec = importlib.util.spec_from_file_location("spiffe_identity", smt__TARGET_PATH)
if smt_spec and smt_spec.loader:
    spiffe_identity = importlib.util.module_from_spec(smt_spec)
    sys.modules[smt_spec.name] = spiffe_identity
    smt_spec.loader.exec_module(spiffe_identity)
else:
    raise ImportError(f"Could not load module from {smt__TARGET_PATH}")

class smt_TestSpiffeIdentityMtls(unittest.TestCase):
    """Test suite for SPIFFE ID parsing, SVID issuance, trust domain validation, and rotation."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory(prefix="mios-test-spiffe-")
        self.cache_file = os.path.join(self.tmpdir.name, "spiffe-cache.json")

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_spiffe_id_formatting_and_parsing(self):
        uri = spiffe_identity.format_spiffe_id("mios.cluster", "node-alpha", "agent-pipe")
        self.assertEqual(uri, "spiffe://mios.cluster/node/node-alpha/workload/agent-pipe")

        parsed = spiffe_identity.parse_spiffe_id(uri)
        self.assertEqual(parsed["trust_domain"], "mios.cluster")
        self.assertEqual(parsed["node_id"], "node-alpha")
        self.assertEqual(parsed["workload"], "agent-pipe")

    def test_spiffe_id_parse_invalid_scheme(self):
        with self.assertRaises(ValueError):
            spiffe_identity.parse_spiffe_id("https://mios.cluster/node/node-01/workload/app")

    def test_issue_svid_mock(self):
        agent = spiffe_identity.SpiffeIdentityAgent(
            trust_domain="mios.cluster",
            node_id="node-01",
            cache_path=self.cache_file,
            mock=True,
        )
        res = agent.issue_svid("mios-llm-light", validity_hours=24)
        self.assertTrue(res["success"])
        self.assertEqual(res["status"], "issued")
        self.assertEqual(
            res["spiffe_id"],
            "spiffe://mios.cluster/node/node-01/workload/mios-llm-light",
        )
        self.assertIn("cert_pem", res["svid"])
        self.assertIn("key_pem", res["svid"])

    def test_validate_svid_valid(self):
        agent = spiffe_identity.SpiffeIdentityAgent(mock=True)
        res = agent.issue_svid("hermes-gateway")
        val = agent.validate_svid(res["svid"])
        self.assertTrue(val["valid"])
        self.assertEqual(val["status"], "valid")
        self.assertEqual(val["workload"], "hermes-gateway")

    def test_validate_svid_expired(self):
        agent = spiffe_identity.SpiffeIdentityAgent(mock=True)
        expired_time = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)).isoformat()
        bad_svid = {
            "spiffe_id": "spiffe://mios.cluster/node/node-01/workload/old-task",
            "expires_at": expired_time,
        }
        val = agent.validate_svid(bad_svid)
        self.assertFalse(val["valid"])
        self.assertEqual(val["status"], "expired")

    def test_validate_svid_trust_domain_mismatch(self):
        agent = spiffe_identity.SpiffeIdentityAgent(trust_domain="mios.cluster", mock=True)
        foreign_svid = {
            "spiffe_id": "spiffe://alien.cluster/node/node-01/workload/infiltrator",
            "expires_at": (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=12)).isoformat(),
        }
        val = agent.validate_svid(foreign_svid)
        self.assertFalse(val["valid"])
        self.assertEqual(val["status"], "trust_domain_mismatch")

    def test_rotate_svids_dynamic_in_memory(self):
        agent = spiffe_identity.SpiffeIdentityAgent(cache_path=self.cache_file, mock=True)
        # Issue one long-lived (24h) and one near-expiry (1h) SVID
        agent.issue_svid("service-fresh", validity_hours=24)
        agent.issue_svid("service-expiring", validity_hours=1)

        cache = agent.load_cache()
        old_expiring_fp = cache["svids"]["service-expiring"]["fingerprint"]

        # Run rotation with min_ttl_hours=4.0 -> service-expiring should rotate, service-fresh should not
        rot_res = agent.rotate_svids(force=False, min_ttl_hours=4.0)
        self.assertTrue(rot_res["success"])
        self.assertIn("service-expiring", rot_res["rotated_workloads"])
        self.assertIn("service-fresh", rot_res["unchanged_workloads"])

        new_cache = agent.load_cache()
        new_expiring_fp = new_cache["svids"]["service-expiring"]["fingerprint"]
        self.assertNotEqual(old_expiring_fp, new_expiring_fp)

    def test_force_rotate_all_svids(self):
        agent = spiffe_identity.SpiffeIdentityAgent(cache_path=self.cache_file, mock=True)
        agent.issue_svid("wl-1", validity_hours=24)
        agent.issue_svid("wl-2", validity_hours=24)

        rot_res = agent.rotate_svids(force=True)
        self.assertEqual(rot_res["total_rotated"], 2)
        self.assertIn("wl-1", rot_res["rotated_workloads"])
        self.assertIn("wl-2", rot_res["rotated_workloads"])

    def test_status_output(self):
        agent = spiffe_identity.SpiffeIdentityAgent(cache_path=self.cache_file, mock=True)
        agent.issue_svid("db-pgvector", validity_hours=24)
        status = agent.get_status()
        self.assertEqual(status["active_svids_count"], 1)
        self.assertEqual(status["trust_domain"], "mios.cluster")
        self.assertEqual(status["svids"][0]["workload"], "db-pgvector")

    def test_cli_execution_mock(self):
        with patch.object(sys, "argv", ["spiffe_identity.py", "--mock", "--issue", "--workload", "cli-test", "--json"]):
            code = spiffe_identity.main()
            self.assertEqual(code, 0)

        with patch.object(sys, "argv", ["spiffe_identity.py", "--mock", "--status", "--json"]):
            code = spiffe_identity.main()
            self.assertEqual(code, 0)


# ==== from tests/test-sec.py (prefix sh_) ====
"""Automated unit test suite for MiOS Systemd Hardening Manager."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "usr", "libexec", "mios", "sec"))

from systemd_harden import SystemdHardeningManager

class sh_TestSystemdHardening(unittest.TestCase):
    def setUp(self):
        self.mgr = SystemdHardeningManager(dry_run=True)

    def test_generate_dropin_contains_core_sandboxing_directives(self):
        """Test generated drop-in includes ProtectSystem, PrivateTmp, and Seccomp filters."""
        dropin = self.mgr.generate_dropin_content("mios-pgvector.service")
        self.assertIn("ProtectSystem=strict", dropin)
        self.assertIn("PrivateTmp=yes", dropin)
        self.assertIn("SystemCallFilter=", dropin)

    def test_hardened_unit_achieves_exposure_under_3_target(self):
        """Test hardened unit achieves security exposure score < 3.0."""
        audit = self.mgr.audit_unit_exposure("mios-hermes.service", has_hardening_dropin=True)
        self.assertTrue(audit.is_safe)
        self.assertLess(audit.exposure_score, 3.0)

    def test_unhardened_unit_fails_security_gate(self):
        """Test unhardened unit is flagged with high exposure score."""
        audit = self.mgr.audit_unit_exposure("raw-daemon.service", has_hardening_dropin=False)
        self.assertFalse(audit.is_safe)
        self.assertGreater(audit.exposure_score, 3.0)


# ==== from tests/test-sec.py (prefix ue_) ====
"""Unit and integration test suite for UkiEnrollEngine and CLI."""


import importlib.util
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ue__HERE = os.path.dirname(os.path.abspath(__file__))
ue__ROOT = os.path.normpath(os.path.join(ue__HERE, ".."))
ue__TARGET_PATH = os.path.join(ue__ROOT, "usr", "libexec", "mios", "sec", "uki_enroll.py")

ue_spec = importlib.util.spec_from_file_location("uki_enroll", ue__TARGET_PATH)
if ue_spec and ue_spec.loader:
    uki_enroll = importlib.util.module_from_spec(ue_spec)
    sys.modules[ue_spec.name] = uki_enroll
    ue_spec.loader.exec_module(uki_enroll)
else:
    raise ImportError(f"Could not load module from {ue__TARGET_PATH}")

class ue_TestUkiEnroll(unittest.TestCase):
    """Test suite for UKI signing key generation, UEFI db enrollment, and TPM2 PCR sealing."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="mios-test-uki-")
        self.key_dir = self.temp_dir.name

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_generate_signing_keys_mock(self):
        engine = uki_enroll.UkiEnrollEngine(mock=True)
        res = engine.generate_signing_keys(key_dir=self.key_dir, key_type="rsa4096")
        self.assertIn("key_path", res)
        self.assertIn("crt_path", res)
        self.assertEqual(res["key_type"], "rsa4096")
        self.assertTrue(os.path.exists(res["key_path"]))
        self.assertTrue(os.path.exists(res["crt_path"]))

        with open(res["key_path"], "r", encoding="utf-8") as f:
            content = f.read()
            self.assertIn("BEGIN PRIVATE KEY", content)

    def test_generate_signing_keys_ed25519_mock(self):
        engine = uki_enroll.UkiEnrollEngine(mock=True)
        res = engine.generate_signing_keys(key_dir=self.key_dir, key_type="ed25519")
        self.assertEqual(res["key_type"], "ed25519")
        self.assertTrue(os.path.exists(res["key_path"]))

    def test_generate_signing_keys_invalid_type_raises(self):
        engine = uki_enroll.UkiEnrollEngine(mock=False)
        with self.assertRaises(ValueError):
            engine.generate_signing_keys(key_dir=self.key_dir, key_type="unsupported_cipher")

    def test_enroll_uefi_db_mock(self):
        engine = uki_enroll.UkiEnrollEngine(mock=True)
        crt_file = os.path.join(self.key_dir, "uki-signing.crt")
        self.assertTrue(engine.enroll_uefi_db(crt_file))

    def test_enroll_uefi_db_missing_file_raises(self):
        engine = uki_enroll.UkiEnrollEngine(mock=False)
        non_existent = os.path.join(self.key_dir, "non_existent.crt")
        with self.assertRaises(FileNotFoundError):
            engine.enroll_uefi_db(non_existent)

    def test_seal_and_unseal_secret_matching_pcrs(self):
        engine = uki_enroll.UkiEnrollEngine(mock=True)
        secret = b"my-super-secret-luks-passphrase"
        nv_idx = 0x1500018

        # Seal
        seal_info = engine.seal_secret_to_pcr(secret=secret, pcr_list=[7, 14], nv_index=nv_idx)
        self.assertEqual(seal_info["nv_index"], hex(nv_idx))
        self.assertEqual(seal_info["pcr_list"], [7, 14])
        self.assertIn("policy_digest", seal_info)
        self.assertIn("sealed_blob", seal_info)

        # Unseal with matching measurements
        unsealed = engine.unseal_secret_from_pcr(nv_index=nv_idx, current_pcrs=seal_info["pcr_hashes"])
        self.assertEqual(unsealed, secret)

    def test_unseal_secret_mismatched_pcrs_raises(self):
        engine = uki_enroll.UkiEnrollEngine(mock=True)
        secret = b"confidential-kernel-key"
        nv_idx = 0x1500019

        seal_info = engine.seal_secret_to_pcr(secret=secret, pcr_list=[7, 14], nv_index=nv_idx)

        # Tamper with PCR 7 measurement
        tampered_pcrs = dict(seal_info["pcr_hashes"])
        tampered_pcrs[7] = "0000000000000000000000000000000000000000000000000000000000000000"

        with self.assertRaises(PermissionError):
            engine.unseal_secret_from_pcr(nv_index=nv_idx, current_pcrs=tampered_pcrs)

    def test_unseal_missing_nv_index_raises(self):
        engine = uki_enroll.UkiEnrollEngine(mock=True)
        with self.assertRaises(RuntimeError):
            engine.unseal_secret_from_pcr(nv_index=0x9999999)

    def test_check_enrollment_status(self):
        engine = uki_enroll.UkiEnrollEngine(mock=True)
        status = engine.check_enrollment_status(key_dir=self.key_dir, nv_index=0x1500018)
        self.assertEqual(status["status"], "ok")
        self.assertTrue(status["uefi_enrolled"])
        self.assertTrue(status["tpm2_sealed"])

    def test_cli_execution_mock_json(self):
        test_args = [
            "uki_enroll.py",
            "--generate-keys",
            "--key-dir", self.key_dir,
            "--key-type", "rsa4096",
            "--enroll-uefi",
            "--seal",
            "--secret", "cli-test-secret",
            "--mock",
            "--json",
        ]
        with patch.object(sys, "argv", test_args):
            exit_code = uki_enroll.main()
            self.assertEqual(exit_code, 0)

    def test_cli_execution_check_only(self):
        test_args = [
            "uki_enroll.py",
            "--check",
            "--mock",
            "--json",
        ]
        with patch.object(sys, "argv", test_args):
            exit_code = uki_enroll.main()
            self.assertEqual(exit_code, 0)

def ue_main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(ue_TestUkiEnroll)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# ==== from tests/test-sec.py (prefix up_) ====
"""Automated unit test suite for MiOS USBGuard Policy Manager."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "usr", "libexec", "mios", "sec"))

from usbguard import USBGuardPolicyManager

class up_TestUSBGuardPolicy(unittest.TestCase):
    def setUp(self):
        self.mgr = USBGuardPolicyManager(dry_run=True)
        self.mgr.enroll_device("046d", "c52b", "SN_AUTH_01", "Logitech Keyboard")

    def test_enrolled_device_allowed(self):
        """Test pre-enrolled peripheral communicates cleanly."""
        allowed = self.mgr.handle_device_insertion(
            "usb1", "046d", "c52b", "SN_AUTH_01", "03:01:01", "Logitech Keyboard"
        )
        self.assertTrue(allowed)
        self.assertEqual(len(self.mgr.blocked_attempts), 0)

    def test_unauthorized_badusb_blocked(self):
        """Test unauthorized rogue USB HID device is blocked by default."""
        allowed = self.mgr.handle_device_insertion(
            "usb2", "1234", "5678", "SN_ROGUE_99", "03:01:01", "Rogue Rubber Ducky"
        )
        self.assertFalse(allowed)
        self.assertEqual(len(self.mgr.blocked_attempts), 1)

    def test_interactive_authorization_and_rule_generation(self):
        """Test operator approval unlocks device and updates generated rules.conf."""
        self.mgr.handle_device_insertion("usb3", "04b4", "f138", "SN_NEW_02", "03:00:00", "Custom Controller")
        ok = self.mgr.authorize_device_interactively("usb3")
        self.assertTrue(ok)

        rules = self.mgr.generate_rules_conf()
        self.assertIn("04b4:f138", rules)
        self.assertIn("SN_NEW_02", rules)


# ==== from tests/test-sec.py (prefix vs_) ====
"""Unit and integration test suite for VramSanitizer and CLI."""


import importlib.util
import json
import os
import sys
import unittest
from unittest.mock import patch

vs__HERE = os.path.dirname(os.path.abspath(__file__))
vs__ROOT = os.path.normpath(os.path.join(vs__HERE, ".."))
vs__TARGET_PATH = os.path.join(vs__ROOT, "usr", "libexec", "mios", "sec", "vram_sanitize.py")

vs_spec = importlib.util.spec_from_file_location("vram_sanitize", vs__TARGET_PATH)
if vs_spec and vs_spec.loader:
    vram_sanitize = importlib.util.module_from_spec(vs_spec)
    sys.modules[vs_spec.name] = vram_sanitize
    vs_spec.loader.exec_module(vram_sanitize)
else:
    raise ImportError(f"Could not load module from {vs__TARGET_PATH}")

def _vs_quadlet_dir() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                        "usr", "share", "containers", "systemd")

class vs_TestVramSanitize(unittest.TestCase):
    """Test suite for multi-vendor GPU discovery, VRAM scrubbing, and Quadlet config audits."""

    def test_discover_gpus_mock(self):
        sanitizer = vram_sanitize.VramSanitizer(mock=True)
        gpus = sanitizer.discover_gpus()
        self.assertGreaterEqual(len(gpus), 2)
        vendors = [g["vendor"] for g in gpus]
        self.assertIn("NVIDIA", vendors)
        self.assertIn("AMD", vendors)

    def test_scrub_gpu_memory_mock(self):
        sanitizer = vram_sanitize.VramSanitizer(mock=True)
        res = sanitizer.scrub_gpu_memory(device_id=0, pattern=b"\x00")
        self.assertTrue(res["success"])
        self.assertEqual(res["device_id"], 0)
        self.assertEqual(res["pattern_used"], "0x00")

    def test_verify_memory_zeroed_mock(self):
        sanitizer = vram_sanitize.VramSanitizer(mock=True)
        self.assertTrue(sanitizer.verify_memory_zeroed(device_id=0))

    def test_audit_quadlet_configs_mock(self):
        sanitizer = vram_sanitize.VramSanitizer(mock=True)
        # The tree's own Quadlets, not whatever the host has installed.
        audit_res = sanitizer.audit_quadlet_configs(quadlet_dir=_vs_quadlet_dir())
        self.assertGreater(audit_res["containers_audited"], 0)
        self.assertTrue(audit_res["audit_passed"])
        self.assertEqual(len(audit_res["findings"]), 0)

    def test_cli_execution_scrub(self):
        test_args = [
            "vram_sanitize.py",
            "--scrub",
            "--pattern", "zero",
            "--mock",
            "--json",
        ]
        with patch.object(sys, "argv", test_args):
            exit_code = vram_sanitize.main()
            self.assertEqual(exit_code, 0)

    def test_cli_execution_verify(self):
        test_args = [
            "vram_sanitize.py",
            "--verify",
            "--device-id", "0",
            "--mock",
            "--json",
        ]
        with patch.object(sys, "argv", test_args):
            exit_code = vram_sanitize.main()
            self.assertEqual(exit_code, 0)

    def test_cli_execution_audit_configs(self):
        test_args = [
            "vram_sanitize.py",
            "--audit-configs",
            "--quadlet-dir", _vs_quadlet_dir(),
            "--mock",
            "--json",
        ]
        with patch.object(sys, "argv", test_args):
            exit_code = vram_sanitize.main()
            self.assertEqual(exit_code, 0)

def vs_main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(vs_TestVramSanitize)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1




class TestOwuiCredentialDiagnostics(unittest.TestCase):
    """Real SQLite creation/reconciliation and failing secret boundaries stay private."""

    @classmethod
    def setUpClass(cls):
        import importlib.machinery
        from pathlib import Path
        target = Path(__file__).resolve().parents[1] / "usr/libexec/mios/mios-owui-bootstrap-admin"
        loader = importlib.machinery.SourceFileLoader("owui_credentials_under_test", str(target))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        cls.admin = importlib.util.module_from_spec(spec)
        loader.exec_module(cls.admin)

    def test_creation_and_reconciliation_do_not_log_identity_or_credentials(self):
        import contextlib
        import io
        import sqlite3
        from pathlib import Path
        marker = "DEVLOOP-PLANTED-PRIVATE"
        email = marker + "@example.invalid"
        with tempfile.TemporaryDirectory(prefix="mios-owui-privacy-") as temporary:
            database = Path(temporary) / "webui.db"
            receipt = Path(temporary) / (marker + "-credentials")
            with sqlite3.connect(database) as connection:
                connection.executescript("CREATE TABLE user(id TEXT, name TEXT, email TEXT, role TEXT, updated_at INTEGER);"
                                         "CREATE TABLE auth(id TEXT, email TEXT, password TEXT, active INTEGER);")
            output = io.StringIO()
            with patch.object(self.admin, "DB", database), patch.object(self.admin, "PASSWORD_OUT", receipt), \
                 patch.object(self.admin, "_read_mios_toml_identity", return_value=(marker, email)), \
                 patch.object(self.admin, "_password_tier", return_value=(0, False)), \
                 patch.object(self.admin, "_read_password", return_value=marker), \
                 patch.object(self.admin, "_bcrypt_hash", return_value="fixture-hash"), contextlib.redirect_stderr(output):
                self.assertEqual(self.admin.main(), 0)
                self.assertEqual(self.admin.main(), 0)
            self.assertNotIn(marker, output.getvalue())
            self.assertIn("admin user created", output.getvalue())
            self.assertIn("reconciled OWUI admin", output.getvalue())
            with sqlite3.connect(database) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM user").fetchone()[0], 1)
                self.assertEqual(connection.execute("SELECT password, active FROM auth").fetchone(), ("fixture-hash", 1))
            self.assertNotIn("password: " + marker, receipt.read_text())
            if os.name != "nt":
                self.assertEqual(receipt.stat().st_mode & 0o777, 0o600)

    def test_failed_hash_and_key_export_do_not_log_exception_payloads(self):
        import contextlib
        import io
        import subprocess
        from unittest.mock import Mock
        marker = "DEVLOOP-PLANTED-PRIVATE"
        output = io.StringIO()
        with patch.object(subprocess, "run", side_effect=FileNotFoundError(marker)), contextlib.redirect_stderr(output):
            with self.assertRaises(SystemExit) as failure:
                self.admin._bcrypt_hash_via_container(marker)
        self.assertEqual(failure.exception.code, 2)
        connection = Mock()
        connection.execute.side_effect = RuntimeError(marker)
        with contextlib.redirect_stderr(output):
            self.admin._export_admin_api_key(connection, "fixture-user")
        self.assertNotIn(marker, output.getvalue())
        self.assertIn("FATAL: bcrypt unavailable", output.getvalue())
        self.assertIn("api_key export skipped", output.getvalue())


def main() -> int:
    rc = 0 if unittest.main(argv=[sys.argv[0]], exit=False).result.wasSuccessful() else 1
    return rc


if __name__ == '__main__':
    sys.exit(main())
