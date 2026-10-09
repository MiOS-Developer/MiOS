#!/usr/bin/env python3
# AI-hint: Consolidated MiOS AI-stack unit tests: accelerator router, audit chain, GPU sched, model matrix, paged attention, prompt cache, self-heal, skills, spec decoding, synthetic QA, kernels, TTS, visual RAG, VRAM.
"""Consolidated unit tests for the MiOS AI stack (usr/libexec/mios/ai)."""
from __future__ import annotations


# ===== merged from tests/test-ai.py (prefix ar_) =====
"""Automated unit test suite for MiOS Hierarchical Accelerator Router."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "usr", "libexec", "mios", "ai"))

from accelerator_router import HierarchicalAcceleratorRouter

class ar_TestAcceleratorRouter(unittest.TestCase):
    def setUp(self):
        self.router_npu = HierarchicalAcceleratorRouter(has_npu=True, dry_run=True)
        self.router_cpu = HierarchicalAcceleratorRouter(has_npu=False, dry_run=True)

    def test_npu_priority_for_embeddings_keeps_dgpu_asleep(self):
        """Test embeddings route to NPU with dGPU power-gated in D3cold."""
        res = self.router_npu.route_inference_task("embedding")
        self.assertEqual(res.assigned_target, "NPU")
        self.assertEqual(res.dgpu_power_state, "D3cold_Sleep")
        self.assertTrue(res.is_power_gated)
        self.assertLess(res.estimated_wattage, 2.0)

    def test_cpu_vector_fallback_when_no_npu_present(self):
        """Test systems without NPU fall back to optimized CPU vector threads."""
        res = self.router_cpu.route_inference_task("wake_word")
        self.assertEqual(res.assigned_target, "CPU_Vector")
        self.assertEqual(res.dgpu_power_state, "D3cold_Sleep")

    def test_heavy_llm_routes_to_dgpu(self):
        """Test heavy 32B coding model wakes dGPU to D0 active."""
        res = self.router_npu.route_inference_task("reasoning_32b")
        self.assertEqual(res.assigned_target, "dGPU_Heavy")
        self.assertEqual(res.dgpu_power_state, "D0_Active")
        self.assertFalse(res.is_power_gated)


# ===== merged from tests/test-ai.py (prefix ac_) =====
"""Unit and integration test suite for AuditChainRecorder and audit_chain CLI (T-554)."""


import copy
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ac__HERE = os.path.dirname(os.path.abspath(__file__))
ac__ROOT = os.path.normpath(os.path.join(ac__HERE, ".."))
ac__TARGET_PATH = os.path.join(ac__ROOT, "usr", "libexec", "mios", "ai", "audit_chain.py")

ac_spec = importlib.util.spec_from_file_location("audit_chain", ac__TARGET_PATH)
if ac_spec and ac_spec.loader:
    ac_audit_chain = importlib.util.module_from_spec(ac_spec)
    sys.modules[ac_spec.name] = ac_audit_chain
    ac_spec.loader.exec_module(ac_audit_chain)
else:
    raise ImportError(f"Could not load module from {ac__TARGET_PATH}")

class ac_TestAuditChain(unittest.TestCase):
    """Test suite for cryptographic audit chain verification, Ed25519 signatures, and Merkle proofs."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory(prefix="mios-test-audit-")
        self.log_path = os.path.join(self.tmpdir.name, "audit_chain.jsonl")
        self.key_path = os.path.join(self.tmpdir.name, "node_ed25519.key")

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_genesis_block_and_chain_creation(self):
        recorder = ac_audit_chain.AuditChainRecorder(
            log_path=self.log_path,
            key_path=self.key_path,
            mock=True,
        )
        blocks = recorder.load_blocks()
        self.assertEqual(len(blocks), 1)
        genesis = blocks[0]
        self.assertEqual(genesis["index"], 0)
        self.assertEqual(genesis["prev_hash"], "0" * 64)
        self.assertEqual(genesis["event_type"], "genesis")

        ver = recorder.verify_chain()
        self.assertTrue(ver["valid"])
        self.assertEqual(ver["status"], "verified")
        self.assertEqual(ver["blocks_verified"], 1)

    def test_record_sequential_events(self):
        recorder = ac_audit_chain.AuditChainRecorder(
            log_path=self.log_path,
            key_path=self.key_path,
            mock=True,
        )

        for i in range(1, 10):
            res = recorder.record_event(
                event_type="file_edit",
                payload={"target_file": f"usr/share/doc/chapter_{i}.md", "lines_changed": i * 10},
            )
            self.assertTrue(res["success"])
            self.assertEqual(res["index"], i)

        blocks = recorder.load_blocks()
        self.assertEqual(len(blocks), 10)

        ver = recorder.verify_chain()
        self.assertTrue(ver["valid"])
        self.assertEqual(ver["blocks_verified"], 10)
        self.assertIsNotNone(ver["merkle_root"])

    def test_merkle_tree_proof_generation_and_verification(self):
        leaf_hashes = [
            ac_audit_chain.hashlib.sha256(f"leaf_{i}".encode("utf-8")).hexdigest()
            for i in range(8)
        ]
        tree = ac_audit_chain.MerkleTree(leaf_hashes)
        root = tree.root

        for idx in range(len(leaf_hashes)):
            proof = tree.get_proof(idx)
            is_valid = ac_audit_chain.MerkleTree.verify_proof(leaf_hashes[idx], proof, root)
            self.assertTrue(is_valid, f"Merkle proof verification failed for leaf index {idx}")

        # Invalid leaf hash must fail verification
        bad_leaf = ac_audit_chain.hashlib.sha256(b"fake_leaf").hexdigest()
        self.assertFalse(ac_audit_chain.MerkleTree.verify_proof(bad_leaf, tree.get_proof(0), root))

    def test_tamper_detection_payload_modified(self):
        recorder = ac_audit_chain.AuditChainRecorder(log_path=self.log_path, key_path=self.key_path, mock=True)
        recorder.record_event("decision", {"action": "deploy_service", "target": "agent-pipe"})
        recorder.record_event("tool_call", {"tool": "bwrap", "status": "executed"})

        blocks = recorder.load_blocks()
        tampered_blocks = copy.deepcopy(blocks)

        # Alter payload of block 1
        tampered_blocks[1]["payload"]["target"] = "malicious_backdoor"

        ver = recorder.verify_chain(tampered_blocks)
        self.assertFalse(ver["valid"])
        self.assertEqual(ver["status"], "payload_tampered")
        self.assertEqual(ver["failed_block_index"], 1)

    def test_tamper_detection_broken_prev_hash(self):
        recorder = ac_audit_chain.AuditChainRecorder(log_path=self.log_path, key_path=self.key_path, mock=True)
        recorder.record_event("decision", {"step": 1})
        recorder.record_event("decision", {"step": 2})

        blocks = recorder.load_blocks()
        tampered_blocks = copy.deepcopy(blocks)

        # Alter prev_hash of block 2
        tampered_blocks[2]["prev_hash"] = "f" * 64

        ver = recorder.verify_chain(tampered_blocks)
        self.assertFalse(ver["valid"])
        self.assertEqual(ver["status"], "broken_hash_link")
        self.assertEqual(ver["failed_block_index"], 2)

    def test_tamper_detection_invalid_signature(self):
        recorder = ac_audit_chain.AuditChainRecorder(log_path=self.log_path, key_path=self.key_path, mock=True)
        recorder.record_event("decision", {"step": "approve"})

        blocks = recorder.load_blocks()
        tampered_blocks = copy.deepcopy(blocks)

        # Invalidate signature
        tampered_blocks[1]["signature"] = "0" * 64

        ver = recorder.verify_chain(tampered_blocks)
        self.assertFalse(ver["valid"])
        self.assertEqual(ver["status"], "signature_invalid")
        self.assertEqual(ver["failed_block_index"], 1)

    def test_file_persistence_and_reload(self):
        recorder = ac_audit_chain.AuditChainRecorder(
            log_path=self.log_path,
            key_path=self.key_path,
            mock=False,
        )
        recorder.record_event("boot", {"status": "firstboot_ok"})
        recorder.record_event("auth", {"user": "mios", "method": "fido2"})

        self.assertTrue(os.path.isfile(self.log_path))

        # Re-instantiate recorder to load from disk
        recorder2 = ac_audit_chain.AuditChainRecorder(
            log_path=self.log_path,
            key_path=self.key_path,
            mock=False,
        )
        blocks = recorder2.load_blocks()
        self.assertEqual(len(blocks), 3)  # Genesis + 2 events

        ver = recorder2.verify_chain(blocks)
        self.assertTrue(ver["valid"])

    def test_cli_execution_mock(self):
        with patch.object(sys, "argv", ["audit_chain.py", "--mock", "--record", "--event", "test_cli", "--json"]):
            code = ac_audit_chain.main()
            self.assertEqual(code, 0)

        with patch.object(sys, "argv", ["audit_chain.py", "--mock", "--verify", "--json"]):
            code = ac_audit_chain.main()
            self.assertEqual(code, 0)

        with patch.object(sys, "argv", ["audit_chain.py", "--mock", "--status", "--json"]):
            code = ac_audit_chain.main()
            self.assertEqual(code, 0)


# ===== merged from tests/test-ai.py (prefix gs_) =====
"""Automated unit test suite for MiOS GPU Compute Stream Priority Scheduler."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "usr", "libexec", "mios", "ai"))

from gpu_sched import (
    HIGH_PRIO_LATENCY_TARGET_MS,
    GPUComputeStreamScheduler,
    StreamPriority,
)

class gs_TestGPUSched(unittest.TestCase):
    def setUp(self):
        self.sched = GPUComputeStreamScheduler(gpu_id=0, dry_run=True)

    def test_job_submission_and_priority_mapping(self):
        """Test compute job creation and priority classification."""
        j_voice = self.sched.submit_job("whisper_stt", StreamPriority.HIGH)
        j_chat = self.sched.submit_job("daily_chat", StreamPriority.NORMAL)
        j_train = self.sched.submit_job("qlora_llama3", StreamPriority.LOW)

        self.assertEqual(j_voice.priority, StreamPriority.HIGH)
        self.assertEqual(j_chat.priority, StreamPriority.NORMAL)
        self.assertEqual(j_train.priority, StreamPriority.LOW)
        self.assertFalse(j_train.is_paused)

    def test_high_priority_preemption_and_sub_50ms_latency(self):
        """Test high-priority turn preempts background tasks and achieves <50ms TTFT."""
        bg_job = self.sched.submit_job("synthetic_qa_gen", StreamPriority.LOW, total_steps=50)
        self.sched.step_background_job(bg_job.job_id, step_count=15)
        self.assertEqual(bg_job.completed_steps, 15)

        # Trigger high-priority voice interaction
        job, ttft_ms = self.sched.execute_high_prio_turn("kokoro_tts_stream", steps=10)
        self.assertTrue(job.is_completed)
        self.assertLess(ttft_ms, HIGH_PRIO_LATENCY_TARGET_MS)

        # Background job must have been paused and then resumed
        self.assertFalse(bg_job.is_paused)
        self.assertEqual(bg_job.completed_steps, 15)

    def test_background_job_resumption_and_completion(self):
        """Test background job advances to 100% completion across multiple preemption events."""
        bg_job = self.sched.submit_job("qlora_batch", StreamPriority.LOW, total_steps=100)

        # Step 1: Run 30 steps
        self.sched.step_background_job(bg_job.job_id, step_count=30)
        self.assertEqual(bg_job.completed_steps, 30)

        # Interrupt 1
        self.sched.execute_high_prio_turn("voice_turn_1")

        # Step 2: Run 40 steps
        self.sched.step_background_job(bg_job.job_id, step_count=40)
        self.assertEqual(bg_job.completed_steps, 70)

        # Interrupt 2
        self.sched.execute_high_prio_turn("voice_turn_2")

        # Step 3: Run remaining 30 steps -> Complete
        self.sched.step_background_job(bg_job.job_id, step_count=30)
        self.assertEqual(bg_job.completed_steps, 100)
        self.assertTrue(bg_job.is_completed)

    def test_scheduler_status_metrics(self):
        """Test scheduler status reporting and preemption event metrics."""
        self.sched.submit_job("bg_task", StreamPriority.LOW, total_steps=20)
        self.sched.execute_high_prio_turn("fast_inference")

        status = self.sched.get_status()
        self.assertEqual(status["gpu_id"], 0)
        self.assertEqual(status["preemption_event_count"], 1)
        self.assertTrue(status["sub_50ms_target_met"])


# ===== merged from tests/test-ai.py (prefix ma_) =====
"""Adversarial Stress Test Suite for Milestone 1: 1. Self-Healing Circuit Breaker & Safe Remediation Engine (T-382)    - Rapid bursts of failures (100 rapid events)    - Multi-unit isolation & interleaved failure/recovery sequences    - Circuit breaker window expiration & quarantine timing    - Invalid / binary / corrupted journal logs    - Malformed & traversal /usr immutability attack paths    - Corrupted state JSON recovery and schema validation    - SafeConfigEditor atomic file operations & error handling  2. Synthetic Training Q&A Data Pipeline (T-383)    - Secret redactor: nested keys (JSON/YAML/TOML/Env), multi-line keys (RSA/EC/SSH), tokens, bearer auth    - Secret redactor: multi-word passwords inside quotes    - Secret redactor: false-positive preservation on standard prose and config keys    - Hierarchical markdown parser: 6-level deep headers, header level jumping, headers inside code blocks    - Unclosed code fences, malformed tables, empty sections, unicode/emoji handling    - Q&A synthesis schema adherence & JSONL single-line validation"""


import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

ma__HERE = os.path.dirname(os.path.abspath(__file__))
ma__ROOT = os.path.normpath(os.path.join(ma__HERE, ".."))
ma__SELF_HEAL_PATH = os.path.join(ma__ROOT, "usr", "libexec", "mios", "ai", "self_heal.py")
ma__SYNTH_QA_PATH = os.path.join(ma__ROOT, "usr", "libexec", "mios", "ai", "synthetic_qa.py")

# Import self_heal
ma_spec_sh = importlib.util.spec_from_file_location("self_heal", ma__SELF_HEAL_PATH)
if ma_spec_sh and ma_spec_sh.loader:
    ma_self_heal = importlib.util.module_from_spec(ma_spec_sh)
    sys.modules[ma_spec_sh.name] = ma_self_heal
    ma_spec_sh.loader.exec_module(ma_self_heal)
else:
    raise ImportError(f"Cannot load self_heal from {ma__SELF_HEAL_PATH}")

# Import synthetic_qa
ma_spec_sq = importlib.util.spec_from_file_location("synthetic_qa", ma__SYNTH_QA_PATH)
if ma_spec_sq and ma_spec_sq.loader:
    ma_synthetic_qa = importlib.util.module_from_spec(ma_spec_sq)
    sys.modules[ma_spec_sq.name] = ma_synthetic_qa
    ma_spec_sq.loader.exec_module(ma_synthetic_qa)
else:
    raise ImportError(f"Cannot load synthetic_qa from {ma__SYNTH_QA_PATH}")

class ma_TestSelfHealAdversarial(unittest.TestCase):
    """Adversarial stress testing for T-382 Self-Healing Code Remediation."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="mios_adv_selfheal_")
        self.state_file = os.path.join(self.temp_dir, "circuit.json")
        self.log_file = os.path.join(self.temp_dir, "self-heal.log")
        self.breaker = ma_self_heal.CircuitBreaker(max_attempts=3, window_seconds=900.0, state_file=self.state_file)
        self.enforcer = ma_self_heal.ImmutabilityEnforcer()
        self.editor = ma_self_heal.SafeConfigEditor(self.enforcer)
        self.healer = ma_self_heal.SelfHealer(
            circuit_breaker=self.breaker,
            enforcer=self.enforcer,
            editor=self.editor,
            log_file=self.log_file,
        )

    def tearDown(self):
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_rapid_burst_failures(self):
        """Stress: 100 rapid failure attempts within milliseconds on a single unit."""
        unit = "burst-service.service"
        t0 = 10000.0

        # Attempt 1: allowed
        self.assertTrue(self.breaker.can_attempt(unit, now=t0))
        self.breaker.record_attempt(unit, success=False, now=t0)

        # Attempt 2: allowed
        self.assertTrue(self.breaker.can_attempt(unit, now=t0 + 0.001))
        self.breaker.record_attempt(unit, success=False, now=t0 + 0.001)

        # Attempt 3: allowed, trips breaker upon failure
        self.assertTrue(self.breaker.can_attempt(unit, now=t0 + 0.002))
        tripped = not self.breaker.record_attempt(unit, success=False, now=t0 + 0.002)
        self.assertTrue(tripped)

        # Attempts 4..100: all must be rejected
        for i in range(3, 100):
            ts = t0 + (i * 0.001)
            self.assertFalse(self.breaker.can_attempt(unit, now=ts), f"Attempt {i+1} should have been rejected")
            self.assertTrue(self.breaker.is_quarantined(unit, now=ts))

    def test_multi_unit_isolation_and_interleaved_lifecycle(self):
        """Stress: 10 distinct units failing and recovering in interleaved order."""
        t = 5000.0
        units = [f"unit-{i:02d}.service" for i in range(10)]

        # Fail each unit once
        for u in units:
            self.assertTrue(self.breaker.can_attempt(u, now=t))
            self.breaker.record_attempt(u, success=False, now=t)
            t += 1.0

        # Fail odd units a second time
        for idx, u in enumerate(units):
            if idx % 2 == 1:
                self.assertTrue(self.breaker.can_attempt(u, now=t))
                self.breaker.record_attempt(u, success=False, now=t)
                t += 1.0

        # Fail unit-03 a third time -> should trip unit-03 only
        u3 = "unit-03.service"
        self.assertTrue(self.breaker.can_attempt(u3, now=t))
        self.breaker.record_attempt(u3, success=False, now=t)
        self.assertTrue(self.breaker.is_quarantined(u3, now=t))
        self.assertFalse(self.breaker.can_attempt(u3, now=t))

        # Other units (e.g. unit-00, unit-01, unit-02) must NOT be quarantined
        for u in ["unit-00.service", "unit-01.service", "unit-02.service", "unit-04.service"]:
            self.assertFalse(self.breaker.is_quarantined(u, now=t))
            self.assertTrue(self.breaker.can_attempt(u, now=t))

        # Recovery of unit-01: success=True resets attempts
        u1 = "unit-01.service"
        self.breaker.record_attempt(u1, success=True, now=t)
        st = self.breaker.get_status(u1)
        self.assertEqual(st["recent_attempts"], 0)
        self.assertFalse(st["quarantined"])

    def test_circuit_breaker_window_pruning(self):
        """Verify that failures outside the 900s window are pruned."""
        unit = "expiring.service"
        t0 = 1000.0

        # 2 failures at t0 and t0+100
        self.breaker.record_attempt(unit, success=False, now=t0)
        self.breaker.record_attempt(unit, success=False, now=t0 + 100)
        self.assertEqual(len(self.breaker.attempts[unit]), 2)

        # Advance past 900s window (t0 + 950s)
        # The first failure (t0) is older than 950 - 900 = 50s, so it should be pruned
        t_future = t0 + 950.0
        self.assertTrue(self.breaker.can_attempt(unit, now=t_future))
        self.breaker._prune(unit, now=t_future)
        self.assertEqual(len(self.breaker.attempts[unit]), 1)  # only t0+100 remains

        # Advance past 1100s -> all should be pruned
        self.breaker._prune(unit, now=t0 + 1100.0)
        self.assertNotIn(unit, self.breaker.attempts)

    def test_immutability_enforcement_exhaustive_paths(self):
        """Stress: Exhaustive attack vectors attempting to bypass /usr protection."""
        bad_paths = [
            "/usr",
            "/usr/",
            "/usr/bin/foo",
            "/usr/lib/systemd/system/mios.service",
            "/usr/share/mios/profile.toml",
            "/usr/libexec/mios/ai/self_heal.py",
            "usr/local/bin/custom",
            "\\usr\\bin\\node",
            "C:\\usr\\bin\\binary",
            "D:/usr/share/doc/mios",
            "usr",
        ]
        for p in bad_paths:
            self.assertFalse(self.enforcer.is_path_safe(p), f"Path should be FORBIDDEN: {p}")
            with self.assertRaises(ma_self_heal.PathViolationError, msg=f"Should raise PathViolationError for {p}"):
                self.enforcer.assert_path_safe(p)

        safe_paths = [
            "/etc/mios/profile.toml",
            "/etc/systemd/system/override.conf",
            "/var/lib/mios/pgvector/data",
            "/var/log/mios/self-heal.log",
            "/tmp/temp_patch.toml",
            "/run/mios/runtime.sock",
            os.path.join(self.temp_dir, "etc", "test.conf"),
        ]
        for p in safe_paths:
            self.assertTrue(self.enforcer.is_path_safe(p), f"Path should be SAFE: {p}")

    def test_remediation_aborts_on_usr_modification(self):
        """Stress: Attempting to remediate a target pointing to /usr must abort with PathViolationError."""
        diagnosis = {
            "unit_name": "malicious.service",
            "exit_code": 1,
            "failure_type": "CONFIG_SYNTAX_ERROR",
            "root_cause": "Tampering attempt",
            "target_files": ["/usr/share/mios/profile.toml"],
            "recommended_action": "patch_config",
            "remediation_patch": {
                "file": "/usr/share/mios/profile.toml",
                "content": "hacked = true\n",
            },
        }
        with self.assertRaises(ma_self_heal.PathViolationError):
            self.healer.apply_remediation(diagnosis)

    def test_diagnose_massive_and_corrupted_journal_logs(self):
        """Stress: Huge logs, binary/control characters, mixed error messages."""
        # 1. Empty logs
        evt_empty = ma_self_heal.FailureEvent(unit_name="empty.service", exit_code=1, error_logs=[])
        diag_empty = self.healer.diagnose_failure(evt_empty)
        self.assertEqual(diag_empty["failure_type"], "PROCESS_NONZERO_EXIT")

        # 2. Huge log line (50,000 characters)
        huge_line = "A" * 50000 + " /etc/mios/config.toml syntax error"
        evt_huge = ma_self_heal.FailureEvent(unit_name="huge.service", exit_code=1, error_logs=[huge_line])
        diag_huge = self.healer.diagnose_failure(evt_huge)
        self.assertEqual(diag_huge["failure_type"], "CONFIG_SYNTAX_ERROR")
        self.assertEqual(diag_huge["target_files"], ["/etc/mios/config.toml"])

        # 3. Unprintable / control characters
        control_chars = "\x00\x01\x02\x03\x04\x1b[31mFATAL: /var/lib/mios/missing_dir directory does not exist\x1b[0m"
        evt_ctrl = ma_self_heal.FailureEvent(unit_name="ctrl.service", exit_code=1, error_logs=[control_chars])
        diag_ctrl = self.healer.diagnose_failure(evt_ctrl)
        self.assertEqual(diag_ctrl["failure_type"], "MISSING_VAR_DIRECTORY")

    def test_safe_config_editor_atomicity_and_parent_creation(self):
        """Verify atomic replacement creates nested directory and backs up existing file."""
        nested_file = os.path.join(self.temp_dir, "etc", "deep", "nested", "service.conf")
        self.editor.patch_file(nested_file, "setting = 1\n", create_backup=True)
        self.assertTrue(os.path.exists(nested_file))

        # Patch again with backup
        self.editor.patch_file(nested_file, "setting = 2\n", create_backup=True)
        with open(nested_file, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), "setting = 2\n")

        # Check backup file exists
        parent_dir = os.path.dirname(nested_file)
        bak_files = [f for f in os.listdir(parent_dir) if "service.conf.bak" in f]
        self.assertEqual(len(bak_files), 1)

class ma_TestSyntheticQAAdversarial(unittest.TestCase):
    """Adversarial stress testing for T-383 Synthetic Training Q&A Data Pipeline."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="mios_adv_synqa_")
        self.parser = ma_synthetic_qa.MarkdownHierarchicalParser()
        self.redactor = ma_synthetic_qa.SecretRedactor()
        self.synthesizer = ma_synthetic_qa.QASynthesizer()
        self.pipeline = ma_synthetic_qa.SyntheticQAPipeline(
            parser=self.parser,
            redactor=self.redactor,
            synthesizer=self.synthesizer,
        )

    def tearDown(self):
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_secret_redactor_nested_keys_and_formats(self):
        """Stress: Various nested assignment syntaxes in JSON, YAML, TOML, and Env."""
        cases = [
            ('password = "SecretPassword123!"', 'password: "<REDACTED_SECRET>"'),
            ('passwd = \'admin_pass_999\'', 'passwd: "<REDACTED_SECRET>"'),
            ('secret_key = "abc123secret"', 'secret_key: "<REDACTED_SECRET>"'),
            ('client_secret = "xyz987secret"', 'client_secret: "<REDACTED_SECRET>"'),
            ('  password: SuperSecretYAML', '  password: "<REDACTED_SECRET>"'),
            ('  secret_key: "TopSecret"', '  secret_key: "<REDACTED_SECRET>"'),
            ('PASSWORD: MyPassword', 'PASSWORD: "<REDACTED_SECRET>"'),
            ('Secret_Key: "Key123"', 'Secret_Key: "<REDACTED_SECRET>"'),
        ]
        for inp, expected_substring in cases:
            redacted = self.redactor.redact(inp)
            self.assertIn(expected_substring, redacted, f"Failed on input: {inp}")

    def test_secret_redactor_multiline_private_keys(self):
        """Stress: Multi-line RSA, EC, and OPENSSH private keys."""
        rsa_key = """-----BEGIN RSA PRIVATE KEY----- MIIEowIBAAKCAQEA0Z3v9x4p... ...MULTILINE DATA... -----END RSA PRIVATE KEY-----"""

        ec_key = """-----BEGIN EC PRIVATE KEY----- MHcCAQEEIIz4... -----END EC PRIVATE KEY-----"""

        ssh_key = """-----BEGIN OPENSSH PRIVATE KEY----- b3BlbnNzaC1rZXktdjEAAAAA... -----END OPENSSH PRIVATE KEY-----"""

        for key in [rsa_key, ec_key, ssh_key]:
            wrapped = f"Config header\n{key}\nConfig footer"
            redacted = self.redactor.redact(wrapped)
            self.assertNotIn("MULTILINE DATA", redacted)
            self.assertNotIn("b3BlbnNzaC", redacted)
            self.assertIn("<REDACTED_PRIVATE_KEY>", redacted)

    def test_secret_redactor_tokens_and_ssh_keys(self):
        """Stress: API tokens, GitHub PATs, Bearer headers, and public keys."""
        text = (
            "API key: sk-abcdef1234567890abcdef1234567890\n"
            "PAT: ghp_123456789012345678901234567890123456\n"
            "Auth: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.e30.ZRrHA1JJ\n"
            "Key: ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGo4 user@host\n"
            "Key2: ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQC user@host\n"
        )
        redacted = self.redactor.redact(text)
        self.assertIn("<REDACTED_API_KEY>", redacted)
        self.assertIn("<REDACTED_GITHUB_TOKEN>", redacted)
        self.assertIn("<REDACTED_BEARER_TOKEN>", redacted)
        self.assertIn("<REDACTED_SSH_KEY>", redacted)
        self.assertNotIn("sk-abcdef1234567890abcdef1234567890", redacted)
        self.assertNotIn("ghp_123456789012345678901234567890123456", redacted)

    def test_secret_redactor_false_positive_preservation(self):
        """Ensure standard architectural text and parameter names are NOT corrupted."""
        benign_prose = (
            "The system prompt defines the authentication architecture.\n"
            "All passwords must meet complexity requirements defined in the security manual.\n"
            "The secret key derivation function uses HKDF-SHA256.\n"
            "Token bucket rate limiting is applied at the gateway.\n"
        )
        redacted = self.redactor.redact(benign_prose)
        self.assertIn("complexity requirements", redacted)
        self.assertIn("HKDF-SHA256", redacted)
        self.assertIn("Token bucket", redacted)

    def test_parser_deeply_nested_headers_and_level_jumping(self):
        """Stress: 6 header levels and abrupt jumps (e.g. # to ####)."""
        md_text = """# Level 1 Title

This is the top level overview chapter of the system architecture.

## Level 2 Component

Description of the component with enough words to satisfy word count threshold.

### Level 3 Subsystem

Detailed subsystem description containing architecture rules and operational details.

###### Level 6 Deep Leaf

Extremely nested operational parameter specifications with full details.

## Level 2 Sibling

A sibling section at level 2 popping all intermediate headers off stack."""
        chunks = self.parser.parse_text(md_text, doc_path="doc/deep.md")
        self.assertGreaterEqual(len(chunks), 4)

        # Check leaf chunk hierarchy
        leaf = next((c for c in chunks if "Level 6 Deep Leaf" in c.section_title), None)
        self.assertIsNotNone(leaf)
        self.assertIn("Level 1 Title", leaf.hierarchy)
        self.assertIn("Level 2 Component", leaf.hierarchy)
        self.assertIn("Level 3 Subsystem", leaf.hierarchy)

        # Check sibling chunk hierarchy
        sibling = next((c for c in chunks if "Level 2 Sibling" in c.section_title), None)
        self.assertIsNotNone(sibling)
        self.assertIn("Level 1 Title", sibling.hierarchy)
        self.assertNotIn("Level 3 Subsystem", sibling.hierarchy)
        self.assertNotIn("Level 6 Deep Leaf", sibling.hierarchy)

    def test_parser_headers_inside_code_blocks(self):
        """Stress: Markdown headers inside code fences must NOT be parsed as headers."""
        md_text = """# Outer Header

Here is an example python script containing comments with pound signs:

```python
# This is a python comment, not a markdown header
# Another comment
def hello():
    return True
```

This section concludes the explanation with sufficient descriptive words."""
        chunks = self.parser.parse_text(md_text, doc_path="doc/code_block.md")
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].section_title, "Outer Header")
        self.assertEqual(len(chunks[0].code_blocks), 1)
        self.assertIn("# This is a python comment", chunks[0].code_blocks[0]["code"])

    def test_parser_unclosed_code_fence_and_malformed_tables(self):
        """Stress: Unclosed code fences and malformed tables must not crash."""
        md_text = """# Unclosed Fence Test

Here is text before unclosed fence.

```sh
echo "unclosed code fence"

| col1 | col2 |
| --- | --- |
| val1 | val2 |

More descriptive text to satisfy the minimum word count threshold for this chunk."""
        chunks = self.parser.parse_text(md_text, doc_path="doc/unclosed.md")
        self.assertEqual(len(chunks), 1)

    def test_parser_unicode_and_special_characters(self):
        """Stress: CJK, emojis, mathematical symbols, special markdown characters."""
        md_text = """# 🚀 MiOS 架构规范 (Architecture)

MiOS 是一个不可变的 bootc/OCI Fedora 工作站与本地 AI 操作系统 🌟。

## 🔐 安全与加密 (Security & Crypto)

采用 Ed25519 签名与 ChaCha20-Poly1305 AEAD 加密算法，保证跨节点通信安全。
数学公式: $\\mathcal{H}(k, m) = \\text{HMAC-SHA256}(k, m)$。"""
        chunks = self.parser.parse_text(md_text, doc_path="doc/unicode.md")
        self.assertGreaterEqual(len(chunks), 2)
        sec_chunk = chunks[1]
        self.assertIn("安全与加密", sec_chunk.section_title)
        self.assertIn("ChaCha20-Poly1305", sec_chunk.content)

    def test_qa_synthesis_and_jsonl_export_schema_compliance(self):
        """Stress: End-to-end multi-chunk generation, redaction, and strict JSONL verification."""
        test_file = os.path.join(self.temp_dir, "input.md")
        with open(test_file, "w", encoding="utf-8") as f:
            f.write(
                "# Secret Storage Subsystem\n\n"
                "The secret storage subsystem manages sensitive configurations.\n\n"
                "## Credential Provisioning\n\n"
                "Credentials are provided via configuration files:\n\n"
                "```toml\n"
                "[auth]\n"
                "password = 'UnredactedSecretPassword123!'\n"
                "api_key = 'sk-123456789012345678901234567890'\n"
                "```\n\n"
                "This ensures the service can authenticate securely against local endpoints.\n"
            )

        chunks = self.pipeline.harvest_docs([self.temp_dir])
        records = self.pipeline.generate_dataset(chunks, redact=True)
        self.assertGreaterEqual(len(records), 1)

        # Verify no secret leaked into synthesized records
        for rec in records:
            for msg in rec["messages"]:
                self.assertNotIn("UnredactedSecretPassword123!", msg["content"])
                self.assertNotIn("sk-123456789012345678901234567890", msg["content"])

        # Export and verify line-by-line JSON validity
        out_jsonl = os.path.join(self.temp_dir, "dataset.jsonl")
        self.pipeline.export_jsonl(records, out_jsonl)

        with open(out_jsonl, "r", encoding="utf-8") as f:
            lines = f.readlines()

        self.assertEqual(len(lines), len(records))
        for line in lines:
            parsed = json.loads(line.strip())
            self.assertIn("messages", parsed)
            self.assertIn("metadata", parsed)
            # Verify system prompt identity
            sys_msg = parsed["messages"][0]
            self.assertEqual(sys_msg["role"], "system")
            self.assertIn("MiOS-Opencode", sys_msg["content"])

def ma_main() -> int:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite([
        loader.loadTestsFromTestCase(ma_TestSelfHealAdversarial),
        loader.loadTestsFromTestCase(ma_TestSyntheticQAAdversarial),
    ])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# ===== merged from tests/test-ai.py (prefix mma_) =====
"""Unit and integration test suite for ModelMatrixAllocator and model_matrix_alloc CLI (T-572)."""


import importlib.util
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

mma__HERE = os.path.dirname(os.path.abspath(__file__))
mma__ROOT = os.path.normpath(os.path.join(mma__HERE, ".."))
mma__TARGET_PATH = os.path.join(mma__ROOT, "usr", "libexec", "mios", "ai", "model_matrix_alloc.py")
with open(os.path.join(mma__ROOT, "usr", "share", "mios", "mios.toml"), "rb") as _f:
    import tomllib
    mma__LLM_LIGHT = tomllib.load(_f)["ports"]["llm_light"]  # expected port comes from SSOT, never a literal

mma_spec = importlib.util.spec_from_file_location("model_matrix_alloc", mma__TARGET_PATH)
if mma_spec and mma_spec.loader:
    mma_model_matrix_alloc = importlib.util.module_from_spec(mma_spec)
    sys.modules[mma_spec.name] = mma_model_matrix_alloc
    mma_spec.loader.exec_module(mma_model_matrix_alloc)
else:
    raise ImportError(f"Could not load module from {mma__TARGET_PATH}")


def mma_ssot_llm_light_port():
    """[ports].llm_light of the vendor tree under test (MIOS_TOML_ROOT, else this checkout)."""
    return int(mma_model_matrix_alloc.mios_toml.vendor_tree(os.environ.get("MIOS_TOML_ROOT") or mma__ROOT)["ports"]["llm_light"])

class mma_TestModelMatrixAlloc(unittest.TestCase):
    """Test suite for hardware-tiered model selection, VRAM headroom limits, and llama-swap projection."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory(prefix="mios-test-model-alloc-")

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_classify_tier(self):
        allocator = mma_model_matrix_alloc.ModelMatrixAllocator(mock=True)
        self.assertEqual(allocator.classify_tier(8.0), "consumer")
        self.assertEqual(allocator.classify_tier(16.0), "prosumer")
        self.assertEqual(allocator.classify_tier(24.0), "prosumer")
        self.assertEqual(allocator.classify_tier(48.0), "poweruser")
        self.assertEqual(allocator.classify_tier(80.0), "poweruser")

    def test_consumer_tier_allocation(self):
        allocator = mma_model_matrix_alloc.ModelMatrixAllocator(mock=True)
        alloc = allocator.allocate_matrix(vram_gb=8.0)
        self.assertEqual(alloc["tier"], "consumer")
        self.assertEqual(alloc["models"]["default"]["name"], "qwen2.5-coder-7b")
        self.assertEqual(alloc["models"]["reasoning"]["name"], "deepseek-r1-distill-qwen-7b")
        self.assertEqual(alloc["models"]["embedding"]["name"], "nomic-embed-text")
        self.assertFalse(alloc["heavy_lane"]["enabled"])
        self.assertIn("Off by default", alloc["heavy_lane"]["reason"])

    def test_prosumer_tier_allocation(self):
        allocator = mma_model_matrix_alloc.ModelMatrixAllocator(mock=True)
        alloc = allocator.allocate_matrix(vram_gb=16.0)
        self.assertEqual(alloc["tier"], "prosumer")
        self.assertEqual(alloc["models"]["default"]["name"], "qwen2.5-coder-14b")
        self.assertEqual(alloc["models"]["reasoning"]["name"], "deepseek-r1-distill-qwen-14b")
        self.assertTrue(alloc["fits_in_vram"])
        self.assertFalse(alloc["heavy_lane"]["enabled"])

    def test_poweruser_tier_allocation(self):
        allocator = mma_model_matrix_alloc.ModelMatrixAllocator(mock=True)
        alloc = allocator.allocate_matrix(vram_gb=48.0)
        self.assertEqual(alloc["tier"], "poweruser")
        self.assertEqual(alloc["models"]["default"]["name"], "qwen2.5-coder-32b")
        self.assertEqual(alloc["models"]["reasoning"]["name"], "deepseek-r1-distill-llama-70b")
        self.assertTrue(alloc["heavy_lane"]["enabled"])
        self.assertEqual(alloc["heavy_lane"]["port_key"], "llm_heavy")

    def test_vram_headroom_reservation(self):
        allocator = mma_model_matrix_alloc.ModelMatrixAllocator(headroom_ratio=0.90, mock=True)
        alloc = allocator.allocate_matrix(vram_gb=24.0)
        allowed = alloc["hardware"]["allowed_vram_budget_gb"]
        self.assertEqual(allowed, 24.0 * 0.90)

    def test_generate_llama_swap_config_schema(self):
        allocator = mma_model_matrix_alloc.ModelMatrixAllocator(mock=True)
        alloc = allocator.allocate_matrix(vram_gb=16.0)
        with patch.dict(os.environ, {"MIOS_PORTS_LLM_LIGHT": ""}):
            conf = allocator.generate_llama_swap_config(alloc)

        self.assertEqual(conf["version"], "1.0")
        self.assertEqual(conf["port"], mma_ssot_llm_light_port())
        self.assertIn("mios-coder", conf["models"])
        self.assertIn("mios-reasoning", conf["models"])
        self.assertIn("nomic-embed-text", conf["models"])
        self.assertEqual(conf["models"]["nomic-embed-text"]["ttl"], 0)

    def test_project_yaml_file_generation(self):
        allocator = mma_model_matrix_alloc.ModelMatrixAllocator(mock=True)
        alloc = allocator.allocate_matrix(vram_gb=16.0)
        yaml_path = os.path.join(self.tmpdir.name, "llama-swap.yaml")

        with patch.dict(os.environ, {"MIOS_PORTS_LLM_LIGHT": ""}):
            success = allocator.project_yaml(yaml_path, alloc)
        self.assertTrue(success)
        self.assertTrue(os.path.isfile(yaml_path))

        with open(yaml_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn(f"port: {mma_ssot_llm_light_port()}", content)
        self.assertIn("mios-coder:", content)
        self.assertIn("nomic-embed-text:", content)

    def test_cli_execution_mock(self):
        with patch.object(sys, "argv", ["model_matrix_alloc.py", "--mock", "--detect", "--json"]):
            code = mma_model_matrix_alloc.main()
            self.assertEqual(code, 0)

        with patch.object(sys, "argv", ["model_matrix_alloc.py", "--mock", "--vram", "24", "--json"]):
            code = mma_model_matrix_alloc.main()
            self.assertEqual(code, 0)


# ===== merged from tests/test-ai.py (prefix pa_) =====
"""Automated unit test suite for MiOS PagedAttention Virtual Block Manager."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "usr", "libexec", "mios", "ai"))

from paged_attn import PagedAttentionBlockManager, PhysicalBlock, SessionTable

class pa_TestPagedAttention(unittest.TestCase):
    def setUp(self):
        self.mgr = PagedAttentionBlockManager(total_blocks=2048, block_size=32, dry_run=True)

    def test_block_allocation_and_free(self):
        """Test allocating and freeing discrete 32-token blocks."""
        ok = self.mgr.allocate_tokens("sess_alpha", 64)
        self.assertTrue(ok)
        self.assertEqual(len(self.mgr.sessions["sess_alpha"].logical_to_physical), 2)

        self.mgr.free_session("sess_alpha")
        self.assertNotIn("sess_alpha", self.mgr.sessions)
        self.assertTrue(all(b.is_free for b in self.mgr.physical_blocks))

    def test_copy_on_write_branch_sharing(self):
        """Test branching sessions share physical blocks until modified via CoW."""
        ok1 = self.mgr.allocate_tokens("sess_parent", 64)
        self.assertTrue(ok1)
        parent_pids = list(self.mgr.sessions["sess_parent"].logical_to_physical)

        ok_b = self.mgr.branch_session("sess_parent", "sess_child")
        self.assertTrue(ok_b)
        child_pids = self.mgr.sessions["sess_child"].logical_to_physical
        self.assertEqual(parent_pids, child_pids)

        for pid in parent_pids:
            self.assertEqual(self.mgr.physical_blocks[pid].ref_count, 2)

        ok_cow = self.mgr.append_tokens_cow("sess_child", 10)
        self.assertTrue(ok_cow)
        new_child_pids = self.mgr.sessions["sess_child"].logical_to_physical
        self.assertNotEqual(parent_pids[-1], new_child_pids[-1])
        self.assertGreater(self.mgr.cow_splits, 0)

    def test_100_session_concurrency_and_low_fragmentation(self):
        """Test 100 concurrent dynamic sessions maintain <4% average waste."""
        for i in range(100):
            tokens = 32 * (i % 5 + 1)
            ok = self.mgr.allocate_tokens(f"sess_{i}", tokens)
            self.assertTrue(ok)

        waste = self.mgr.compute_fragmentation_waste()
        self.assertLessEqual(waste, 4.0)

    def test_lru_page_eviction_under_vram_pressure(self):
        """Test that reaching capacity triggers LRU eviction of old sessions."""
        small_mgr = PagedAttentionBlockManager(total_blocks=5, block_size=16, dry_run=True)
        small_mgr.allocate_tokens("sess_old", 80)
        self.assertEqual(small_mgr.free_blocks_count, 0)

        ok = small_mgr.allocate_tokens("sess_new", 32)
        self.assertTrue(ok)
        self.assertIn("sess_new", small_mgr.sessions)
        self.assertNotIn("sess_old", small_mgr.sessions)
        self.assertGreater(small_mgr.evictions, 0)

    def test_asynchronous_defragmentation(self):
        """Test compaction moves fragmented physical blocks to contiguous low-index range."""
        self.mgr.allocate_tokens("s1", 32)
        self.mgr.allocate_tokens("s2", 32)
        self.mgr.allocate_tokens("s3", 32)

        self.mgr.free_session("s2")
        moved = self.mgr.defragment_memory()
        self.assertGreaterEqual(moved, 0)

        stats = self.mgr.get_stats()
        self.assertEqual(stats["active_sessions"], 2)


# ===== merged from tests/test-ai.py (prefix pc_) =====
"""Automated unit test suite for MiOS Radix Tree Prefix Hash Cache Manager."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "usr", "libexec", "mios", "ai"))

from prompt_cache import RadixPromptCacheManager, TTFT_TARGET_MS, MATCH_LATENCY_MAX_MS

class pc_TestPromptCache(unittest.TestCase):
    def setUp(self):
        self.cache = RadixPromptCacheManager(max_cache_mb=1024.0, dry_run=True)

    def test_prefix_insertion_and_cache_hit(self):
        """Test storing and hitting cached system prompt token prefixes with sub-10ms match latency."""
        system_prompt_tokens = [1, 256, 89, 4421, 981, 102, 55, 912, 1004, 302, 11, 44, 99, 120, 881, 402]
        h = self.cache.insert_prefix(system_prompt_tokens)
        self.assertIsNotNone(h)
        self.assertNotEqual(h, "")

        hit, node, match_latency = self.cache.match_prefix(system_prompt_tokens, min_prefix_len=16)
        self.assertTrue(hit)
        self.assertIsNotNone(node)
        self.assertEqual(node.tokens, system_prompt_tokens)
        self.assertLess(match_latency, MATCH_LATENCY_MAX_MS)

    def test_zero_token_loss_on_prefix_match(self):
        """Test that returned prefix node maintains bit-for-bit exact token sequences."""
        tokens = list(range(500, 564))
        self.cache.insert_prefix(tokens)
        hit, node, _ = self.cache.match_prefix(tokens + [9999], min_prefix_len=16)
        self.assertTrue(hit)
        self.assertEqual(node.tokens, tokens)

    def test_high_concurrency_hit_rate(self):
        """Test 50 consecutive queries sharing common system prompt achieve >95% hit rate."""
        sys_tokens = list(range(50, 100))
        self.cache.insert_prefix(sys_tokens)

        for i in range(50):
            query_tokens = sys_tokens + [1000 + i, 2000 + i]
            hit, node, match_latency = self.cache.match_prefix(query_tokens, min_prefix_len=16)
            self.assertTrue(hit)
            self.assertLess(match_latency, MATCH_LATENCY_MAX_MS)

        stats = self.cache.get_stats()
        self.assertGreaterEqual(stats["hit_rate_pct"], 95.0)
        self.assertTrue(stats["sub_20ms_target_met"])
        self.assertGreater(stats["tokens_saved"], 0)

    def test_lru_memory_eviction(self):
        """Test LRU eviction reclaims memory when max_cache_mb is reached."""
        small_cache = RadixPromptCacheManager(max_cache_mb=1.0, dry_run=True)
        for i in range(20):
            prefix = [i + 1] + list(range(100, 130))
            small_cache.insert_prefix(prefix, kv_state_bytes=65536)

        self.assertLessEqual(small_cache.total_memory_bytes, 1048576)

    def test_openai_chat_messages_slot_reuse(self):
        """Test parsing OpenAI chat messages and coordinating slot reuse."""
        messages = [
            {"role": "system", "content": "You are MiOS agent assistant with full local MCP tool capabilities."},
            {"role": "user", "content": "Query energy telemetry status."},
        ]
        tokens = self.cache.parse_openai_chat_messages(messages)
        self.assertGreater(len(tokens), 10)

        # Cold request
        res1 = self.cache.coordinate_slot_reuse("sess_1", tokens)
        self.assertIn("session_id", res1)

        # Warm request
        res2 = self.cache.coordinate_slot_reuse("sess_2", tokens)
        self.assertTrue(res2["prefix_cache_hit"])
        self.assertLess(res2["match_latency_ms"], MATCH_LATENCY_MAX_MS)
        self.assertLess(res2["estimated_ttft_ms"], TTFT_TARGET_MS)


# ===== merged from tests/test-ai.py (prefix sh_) =====
"""
Automated unit tests for systemd failure parsing, journald log diagnosis,
circuit breaker rate limiting, /usr immutability protection, and RCA logging.
"""


import importlib.util
import json
import os
import sys
import tempfile
import time
import unittest

sh__HERE = os.path.dirname(os.path.abspath(__file__))
sh__ROOT = os.path.normpath(os.path.join(sh__HERE, ".."))
sh__SELF_HEAL_PATH = os.path.join(sh__ROOT, "usr", "libexec", "mios", "ai", "self_heal.py")

sh_spec = importlib.util.spec_from_file_location("self_heal", sh__SELF_HEAL_PATH)
if sh_spec and sh_spec.loader:
    sh_self_heal = importlib.util.module_from_spec(sh_spec)
    sys.modules[sh_spec.name] = sh_self_heal
    sh_spec.loader.exec_module(sh_self_heal)
else:
    raise ImportError(f"Could not load self_heal module from {sh__SELF_HEAL_PATH}")

class sh_TestSelfHealing(unittest.TestCase):
    """Validates self-healing agent diagnostics, circuit breaker, and immutability invariants."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="mios_test_selfheal_")
        self.log_file = os.path.join(self.temp_dir, "self-heal.log")
        self.state_file = os.path.join(self.temp_dir, "circuit.json")
        self.breaker = sh_self_heal.CircuitBreaker(max_attempts=3, window_seconds=900.0, state_file=self.state_file)
        self.enforcer = sh_self_heal.ImmutabilityEnforcer()
        self.editor = sh_self_heal.SafeConfigEditor(self.enforcer)
        self.healer = sh_self_heal.SelfHealer(
            circuit_breaker=self.breaker,
            enforcer=self.enforcer,
            editor=self.editor,
            log_file=self.log_file,
        )

    def tearDown(self):
        # Clean up temp files
        import shutil
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_failure_event_serialization(self):
        event = sh_self_heal.FailureEvent(
            unit_name="mios-test.service",
            exit_code=2,
            error_logs=["Error: invalid token on line 4", "Fatal config parse fail"],
        )
        d = event.to_dict()
        self.assertEqual(d["unit_name"], "mios-test.service")
        self.assertEqual(d["exit_code"], 2)
        self.assertEqual(len(d["error_logs"]), 2)

        restored = sh_self_heal.FailureEvent.from_dict(d)
        self.assertEqual(restored.unit_name, event.unit_name)
        self.assertEqual(restored.error_logs, event.error_logs)

    def test_circuit_breaker_rate_limiting_and_quarantine(self):
        unit = "mios-failing.service"
        now = 1000.0

        # Attempts 1, 2 should be allowed
        self.assertTrue(self.breaker.can_attempt(unit, now=now))
        self.breaker.record_attempt(unit, success=False, now=now)

        self.assertTrue(self.breaker.can_attempt(unit, now=now + 10))
        self.breaker.record_attempt(unit, success=False, now=now + 10)

        # Attempt 3 allowed, but recording 3rd failure trips breaker
        self.assertTrue(self.breaker.can_attempt(unit, now=now + 20))
        tripped = not self.breaker.record_attempt(unit, success=False, now=now + 20)
        self.assertTrue(tripped)

        # Subsequent check must show quarantined
        self.assertFalse(self.breaker.can_attempt(unit, now=now + 30))
        self.assertTrue(self.breaker.is_quarantined(unit, now=now + 30))

        # Reset should unquarantine
        self.breaker.reset(unit)
        self.assertTrue(self.breaker.can_attempt(unit, now=now + 40))
        self.assertFalse(self.breaker.is_quarantined(unit, now=now + 40))

    def test_immutability_protection_usr_forbidden(self):
        # Law 1 (USR-OVER-ETC): /usr is strictly immutable
        self.assertFalse(self.enforcer.is_path_safe("/usr/bin/mios-llm"))
        self.assertFalse(self.enforcer.is_path_safe("/usr/share/mios/profile.toml"))
        self.assertFalse(self.enforcer.is_path_safe("usr/lib/systemd/system/foo.service"))

        # Overrides in /etc and runtime state in /var are allowed
        self.assertTrue(self.enforcer.is_path_safe("/etc/mios/profile.toml"))
        self.assertTrue(self.enforcer.is_path_safe("/etc/systemd/system/foo.service"))
        self.assertTrue(self.enforcer.is_path_safe("/var/lib/mios/state.json"))
        self.assertTrue(self.enforcer.is_path_safe("/tmp/scratch.txt"))

        with self.assertRaises(sh_self_heal.PathViolationError):
            self.enforcer.assert_path_safe("/usr/libexec/mios/daemon")

    def test_safe_config_patch_and_backup(self):
        test_file = os.path.join(self.temp_dir, "etc", "test_config.toml")
        os.makedirs(os.path.dirname(test_file), exist_ok=True)
        with open(test_file, "w", encoding="utf-8") as f:
            f.write("port = 8080\n")

        # Patch file
        self.editor.patch_file(test_file, "port = 9090\n", create_backup=True)

        with open(test_file, "r", encoding="utf-8") as f:
            new_content = f.read()
        self.assertEqual(new_content, "port = 9090\n")

        # Verify backup was created
        backups = [f for f in os.listdir(os.path.dirname(test_file)) if "test_config.toml.bak" in f]
        self.assertGreaterEqual(len(backups), 1)

    def test_diagnose_missing_var_directory(self):
        event = sh_self_heal.FailureEvent(
            unit_name="mios-pgvector.service",
            exit_code=1,
            error_logs=[
                "FATAL: data directory does not exist: /var/lib/mios/pgvector/data",
                "Process terminated with exit status 1",
            ],
        )
        diagnosis = self.healer.diagnose_failure(event)
        self.assertEqual(diagnosis["failure_type"], "MISSING_VAR_DIRECTORY")
        self.assertEqual(diagnosis["recommended_action"], "create_var_dir")
        self.assertIn("/var/lib/mios/pgvector/data", diagnosis["target_files"])

    def test_diagnose_config_syntax_error(self):
        event = sh_self_heal.FailureEvent(
            unit_name="mios-gateway.service",
            exit_code=1,
            error_logs=[
                "Failed to parse /etc/mios/gateway.toml: syntax error on line 12",
                "Exiting on initialization error",
            ],
        )
        diagnosis = self.healer.diagnose_failure(event)
        self.assertEqual(diagnosis["failure_type"], "CONFIG_SYNTAX_ERROR")
        self.assertEqual(diagnosis["recommended_action"], "patch_config")
        self.assertIn("/etc/mios/gateway.toml", diagnosis["target_files"])

    def test_diagnose_port_conflict(self):
        event = sh_self_heal.FailureEvent(
            unit_name="mios-hermes.service",
            exit_code=1,
            error_logs=[
                "Error: Address already in use (:8642)",
                "Failed to bind socket",
            ],
        )
        diagnosis = self.healer.diagnose_failure(event)
        self.assertEqual(diagnosis["failure_type"], "PORT_CONFLICT")
        self.assertEqual(diagnosis["recommended_action"], "restart_with_backoff")

    def test_diagnose_and_reject_usr_tampering(self):
        event = sh_self_heal.FailureEvent(
            unit_name="mios-rogue.service",
            exit_code=1,
            error_logs=[
                "Attempted write to /usr/bin/custom_binary: Read-only file system",
                "Failed to update binary",
            ],
        )
        diagnosis = self.healer.diagnose_failure(event)
        self.assertEqual(diagnosis["failure_type"], "IMMUTABLE_PATH_TARGET")
        self.assertEqual(diagnosis["recommended_action"], "quarantine")

    def test_full_remediation_cycle_and_rca_logging(self):
        target_dir = os.path.join(self.temp_dir, "var", "lib", "mios", "test_store")
        fake_unit = "mios-test-store.service"

        event = sh_self_heal.FailureEvent(
            unit_name=fake_unit,
            exit_code=1,
            error_logs=[f"No such file or directory: {target_dir}"],
        )

        diagnosis = self.healer.diagnose_failure(event)
        res = self.healer.apply_remediation(diagnosis)

        self.assertTrue(res["success"])
        self.assertTrue(os.path.exists(target_dir))

        # Verify RCA log was written
        self.assertTrue(os.path.exists(self.log_file))
        with open(self.log_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
        self.assertGreaterEqual(len(lines), 1)
        last_rca = json.loads(lines[-1])
        self.assertEqual(last_rca["unit_name"], fake_unit)
        self.assertEqual(last_rca["failure_type"], "MISSING_VAR_DIRECTORY")

def sh_main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(sh_TestSelfHealing)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# ===== merged from tests/test-ai.py (prefix ss_) =====
"""Automated tests for WS-ORCH trace distillation and SKILL.md markdown generation."""


import importlib.util
import os
import sys
import unittest

ss__HERE = os.path.dirname(os.path.abspath(__file__))
ss__ROOT = os.path.normpath(os.path.join(ss__HERE, ".."))
ss__SYNTH_PATH = os.path.join(ss__ROOT, "usr", "libexec", "mios", "ai", "skill-synthesizer.py")

ss_spec = importlib.util.spec_from_file_location("skill_synthesizer", ss__SYNTH_PATH)
if ss_spec and ss_spec.loader:
    ss_skill_synthesizer = importlib.util.module_from_spec(ss_spec)
    sys.modules[ss_spec.name] = ss_skill_synthesizer
    ss_spec.loader.exec_module(ss_skill_synthesizer)
else:
    raise ImportError(f"Could not load skill-synthesizer module from {ss__SYNTH_PATH}")

class ss_TestSkillSynthesizer(unittest.TestCase):
    """Validates SKILL.md template generation and step formatting."""

    def test_skill_synthesis(self):
        synth = ss_skill_synthesizer.SkillSynthesizer()
        tools = ["view_file", "replace_file_content", "run_command"]
        doc = synth.synthesize_skill_md(
            skill_name="code_refactor_pipeline",
            description="Automated multi-file code refactoring and validation",
            tool_sequence=tools
        )
        self.assertIn("name: code_refactor_pipeline", doc)
        self.assertIn("Step 1: Use `view_file`", doc)
        self.assertIn("Step 2: Use `replace_file_content`", doc)
        self.assertIn("Step 3: Use `run_command`", doc)

def ss_main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(ss_TestSkillSynthesizer)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# ===== merged from tests/test-ai.py (prefix sd_) =====
"""Automated unit test suite for MiOS Speculative Decoding Manager."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "usr", "libexec", "mios", "ai"))

from speculative import SpeculativeDraftManager

class sd_TestSpeculativeDecoding(unittest.TestCase):
    def setUp(self):
        self.mgr = SpeculativeDraftManager(dry_run=True)

    def test_paired_draft_model_lookup(self):
        """Test Qwen-32B pairs with lightweight Qwen-0.5B draft model."""
        draft = self.mgr.get_draft_model("qwen2.5-32b-instruct.Q4_K_M.gguf")
        self.assertEqual(draft, "qwen2.5-0.5b-instruct.Q4_K_M.gguf")

    def test_adaptive_draft_length_scaling(self):
        """Test high acceptance rate expands draft length from 5 to 6."""
        model = "qwen2.5-32b-instruct.Q4_K_M.gguf"
        # Simulate high acceptance (5 of 5 accepted for 3 steps)
        for _ in range(3):
            new_len = self.mgr.update_acceptance_rate(model, accepted_tokens=5, drafted_tokens=5)
        self.assertGreaterEqual(new_len, 6)

    def test_speedup_benchmark_target(self):
        """Test speculative decoding model achieves >=2.5x generation acceleration."""
        res = self.mgr.benchmark_speedup("qwen2.5-32b-instruct.Q4_K_M.gguf")
        self.assertGreaterEqual(res["speedup_ratio"], 2.5)
        self.assertTrue(res["meets_target"])


# ===== merged from tests/test-ai.py (prefix sq_) =====
"""
Automated unit tests for hierarchical markdown header parsing, context preservation,
multi-turn Q&A synthesis, secret and token redaction, and JSONL dataset generation.
"""


import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest

sq__HERE = os.path.dirname(os.path.abspath(__file__))
sq__ROOT = os.path.normpath(os.path.join(sq__HERE, ".."))
sq__SYNTH_QA_PATH = os.path.join(sq__ROOT, "usr", "libexec", "mios", "ai", "synthetic_qa.py")

sq_spec = importlib.util.spec_from_file_location("synthetic_qa", sq__SYNTH_QA_PATH)
if sq_spec and sq_spec.loader:
    sq_synthetic_qa = importlib.util.module_from_spec(sq_spec)
    sys.modules[sq_spec.name] = sq_synthetic_qa
    sq_spec.loader.exec_module(sq_synthetic_qa)
else:
    raise ImportError(f"Could not load synthetic_qa module from {sq__SYNTH_QA_PATH}")

class sq_TestSyntheticQAPipeline(unittest.TestCase):
    """Validates markdown parsing, secret redaction, Q&A synthesis, and JSONL export."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="mios_test_synqa_")
        self.output_jsonl = os.path.join(self.temp_dir, "synthetic_dataset.jsonl")
        self.parser = sq_synthetic_qa.MarkdownHierarchicalParser()
        self.redactor = sq_synthetic_qa.SecretRedactor()
        self.synthesizer = sq_synthetic_qa.QASynthesizer()
        self.pipeline = sq_synthetic_qa.SyntheticQAPipeline(
            parser=self.parser,
            redactor=self.redactor,
            synthesizer=self.synthesizer,
        )

    def tearDown(self):
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_hierarchical_markdown_parsing(self):
        sample_md = """# MiOS Architectural Specification

Overview of the immutable bootc Fedora workstation and local AI operating system.

## Memory Subsystem

The memory subsystem manages pgvector persistence and KV-cache paging.

### PostgreSQL and PgVector Configuration

Here is the setup configuration for pgvector:

```toml
[pgvector]
port = 5432
data_dir = "/var/lib/mios/pgvector"
```

| Parameter | Default | Purpose |
| --- | --- | --- |
| port | 5432 | Database port |
| mode | dedicated | Service lifecycle |
"""
        chunks = self.parser.parse_text(sample_md, doc_path="doc/arch.md")
        self.assertGreaterEqual(len(chunks), 2)

        # Find the pgvector subsection chunk
        pg_chunk = next(c for c in chunks if "PostgreSQL" in c.section_title)
        self.assertEqual(pg_chunk.section_title, "PostgreSQL and PgVector Configuration")
        self.assertIn("Memory Subsystem", pg_chunk.hierarchy)
        self.assertEqual(len(pg_chunk.code_blocks), 1)
        self.assertEqual(pg_chunk.code_blocks[0]["lang"], "toml")
        self.assertIn("port = 5432", pg_chunk.code_blocks[0]["code"])
        self.assertEqual(len(pg_chunk.tables), 1)

    def test_secret_and_token_redaction(self):
        raw_text = (
            "Connecting to cluster using api_key = 'sk-abcdef1234567890abcdef1234567890' "
            "with Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.secret and password='SuperSecretPassword123!'."
        )
        redacted = self.redactor.redact(raw_text)
        self.assertNotIn("sk-abcdef1234567890", redacted)
        self.assertNotIn("SuperSecretPassword123!", redacted)
        self.assertIn("<REDACTED_API_KEY>", redacted)
        self.assertIn("<REDACTED_SECRET>", redacted)

    def test_private_key_redaction(self):
        key_text = (
            "-----BEGIN OPENSSH PRIVATE KEY-----\n"
            "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAACFwAAAAdzc2gtcn\n"
            "-----END OPENSSH PRIVATE KEY-----"
        )
        redacted = self.redactor.redact(f"Host key: {key_text}")
        self.assertNotIn("b3BlbnNzaC", redacted)
        self.assertIn("<REDACTED_PRIVATE_KEY>", redacted)

    def test_qa_synthesis_format_and_roles(self):
        chunk = sq_synthetic_qa.MarkdownChunk(
            doc_path="cat/ADR-0008.md",
            doc_title="ADR 0008",
            section_title="Unified Installer Surface",
            hierarchy=["ADR 0008", "Unified Installer Surface"],
            content="The unified installer coordinates Total Root Merge in Phase-1, overlaying bootstrap files on target root.",
            code_blocks=[{"lang": "bash", "code": "bash install.sh --merge-root"}],
        )

        records = self.synthesizer.synthesize_qa_pairs(chunk)
        self.assertGreaterEqual(len(records), 2)

        for rec in records:
            self.assertIn("messages", rec)
            self.assertIn("metadata", rec)
            msgs = rec["messages"]
            self.assertEqual(msgs[0]["role"], "system")
            self.assertEqual(msgs[1]["role"], "user")
            self.assertEqual(msgs[2]["role"], "assistant")
            self.assertIn("MiOS", msgs[0]["content"])

    def test_end_to_end_harvest_and_jsonl_export(self):
        doc_file = os.path.join(self.temp_dir, "test_guide.md")
        with open(doc_file, "w", encoding="utf-8") as f:
            f.write(
                "# Node Discovery Protocol\n\n"
                "Nodes communicate over 16-byte fixed binary frames using UDP gossip and TCP framing.\n\n"
                "## Frame Header Structure\n\n"
                "The header structure contains magic 0x4D49, version, opcode, node ID, payload len, and CRC32.\n\n"
                "```rust\npub struct FrameHeader {\n    magic: u16,\n    version: u8,\n}\n```\n"
            )

        chunks = self.pipeline.harvest_docs([self.temp_dir])
        self.assertGreaterEqual(len(chunks), 1)

        records = self.pipeline.generate_dataset(chunks, max_samples=5)
        self.assertGreaterEqual(len(records), 1)

        exported_count = self.pipeline.export_jsonl(records, self.output_jsonl)
        self.assertEqual(exported_count, len(records))
        self.assertTrue(os.path.exists(self.output_jsonl))

        # Validate valid JSON on every line
        with open(self.output_jsonl, "r", encoding="utf-8") as f:
            lines = f.readlines()
        self.assertEqual(len(lines), len(records))
        for line in lines:
            parsed = json.loads(line)
            self.assertIn("messages", parsed)
            self.assertGreaterEqual(len(parsed["messages"]), 3)

def sq_main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(sq_TestSyntheticQAPipeline)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# ===== merged from tests/test-ai.py (prefix tk_) =====
"""Automated unit test suite for MiOS GPU Tensor Kernel Dispatcher."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "usr", "libexec", "mios", "ai"))

from tensor_kernels import TensorKernelDispatcher

class tk_TestTensorKernels(unittest.TestCase):
    def setUp(self):
        self.dispatcher = TensorKernelDispatcher(dry_run=True)

    def test_rtx4090_probe_and_env_bindings(self):
        """Test RTX 4090 probe selects SM_89 and FlashAttention-3."""
        arch = self.dispatcher.probe_gpu_capability("NVIDIA GeForce RTX 4090")
        self.assertEqual(arch.sm_version, "sm_89")
        self.assertTrue(arch.flash_attn_supported)

        env = self.dispatcher.get_env_bindings()
        self.assertEqual(env["FLASH_ATTN_VERSION"], "3")
        self.assertEqual(env["CUTLASS_SM_ARCH"], "89")

    def test_tensor_throughput_benchmark_target(self):
        """Test kernel dispatch achieves >90% theoretical peak efficiency."""
        self.dispatcher.probe_gpu_capability("NVIDIA GeForce RTX 4090")
        res = self.dispatcher.benchmark_throughput(batch_size=16)
        self.assertTrue(res["meets_target"])
        self.assertGreaterEqual(res["efficiency_pct"], 90.0)


# ===== merged from tests/test-ai.py (prefix ts_) =====
"""Automated unit test suite for MiOS Streaming TTS Pipeline."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "usr", "libexec", "mios", "ai"))

from tts_stream import MAX_FIRST_PACKET_LATENCY_MS, StreamingTTSPipeline

class ts_TestTTSStream(unittest.TestCase):
    def setUp(self):
        self.pipe = StreamingTTSPipeline(dry_run=True)

    def test_sub_50ms_first_packet_audio_latency(self):
        """Test streaming TTS delivers first audio chunk in <50ms."""
        res = self.pipe.stream_speech_synthesis("System ready. All agents synchronized.")
        self.assertLess(res.first_packet_latency_ms, MAX_FIRST_PACKET_LATENCY_MS)
        self.assertGreater(res.chunks_generated, 0)
        self.assertEqual(res.buffer_underruns_detected, 0)

    def test_multi_sentence_streaming_zero_underruns(self):
        """Test 20 sentence stream maintains 0 buffer underruns."""
        for i in range(20):
            res = self.pipe.stream_speech_synthesis(f"Sentence number {i} with audio chunks streaming.")
            self.assertEqual(res.buffer_underruns_detected, 0)
            self.assertLess(res.first_packet_latency_ms, MAX_FIRST_PACKET_LATENCY_MS)


# ===== merged from tests/test-ai.py (prefix vr_) =====
"""Automated tests for WS-AI visual state hashing and metadata generation."""


import importlib.util
import os
import sys
import unittest

vr__HERE = os.path.dirname(os.path.abspath(__file__))
vr__ROOT = os.path.normpath(os.path.join(vr__HERE, ".."))
vr__VIS_PATH = os.path.join(vr__ROOT, "usr", "libexec", "mios", "ai", "visual-rag.py")

vr_spec = importlib.util.spec_from_file_location("visual_rag", vr__VIS_PATH)
if vr_spec and vr_spec.loader:
    vr_visual_rag = importlib.util.module_from_spec(vr_spec)
    sys.modules[vr_spec.name] = vr_visual_rag
    vr_spec.loader.exec_module(vr_visual_rag)
else:
    raise ImportError(f"Could not load visual-rag module from {vr__VIS_PATH}")

class vr_TestVisualRag(unittest.TestCase):
    """Validates screenshot hashing and visual metadata record schema."""

    def test_screenshot_indexing(self):
        indexer = vr_visual_rag.VisualRAGIndexer(mock_dim=512)
        sample_frame = b"PNG_FAKE_IMAGE_BYTES_12345"
        record = indexer.process_screenshot(sample_frame, window_title="Terminal - MiOS")
        self.assertEqual(record["status"], "indexed")
        self.assertEqual(record["window_title"], "Terminal - MiOS")
        self.assertEqual(record["byte_size"], len(sample_frame))
        self.assertEqual(len(record["image_hash"]), 64)

def vr_main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(vr_TestVisualRag)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# ===== merged from tests/test-ai.py (prefix vs_) =====
"""Automated unit test suite for MiOS Dynamic VRAM Swapper and LRU KV Pager."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "usr", "libexec", "mios", "ai"))

from vram_swap import MAX_SWAP_LATENCY_MS, VRAMSwapManager

class vs_TestVRAMSwap(unittest.TestCase):
    def setUp(self):
        # 8 GB VRAM test budget with fast PCIe 4.0
        self.mgr = VRAMSwapManager(
            total_vram_mb=8192.0,
            total_host_ram_mb=32768.0,
            pcie_bandwidth_gbps=32.0,
            vram_watermark_ratio=0.80,
            dry_run=True,
        )
        self.mgr.register_model("mios-opencode", total_layers=32, layer_size_mb=128.0)
        self.mgr.register_model("mios-chat", total_layers=32, layer_size_mb=128.0)
        self.mgr.register_model("mios-vision", total_layers=32, layer_size_mb=160.0)

    def test_sub_500ms_model_swapping(self):
        """Test multi-model switching occurs in under 500ms."""
        models = ["mios-opencode", "mios-chat", "mios-vision", "mios-opencode"]
        for model in models:
            ok, latency_ms = self.mgr.activate_model(model)
            self.assertTrue(ok)
            self.assertLess(latency_ms, MAX_SWAP_LATENCY_MS)

        status = self.mgr.get_status()
        self.assertTrue(status["sub_500ms_target_met"])

    def test_lru_kv_cache_paging_under_pressure(self):
        """Test that inactive KV cache slots page out to host RAM when VRAM fills up."""
        # Activate model taking 4096 MB
        self.mgr.activate_model("mios-opencode")

        # Create session 1 and unpin it (now idle)
        s1 = self.mgr.allocate_or_update_kv_slot("sess_1", "mios-opencode", token_count=1000, size_mb=1024.0)
        self.mgr.unpin_kv_slot("sess_1")

        # Create session 2 and unpin it
        s2 = self.mgr.allocate_or_update_kv_slot("sess_2", "mios-opencode", token_count=2000, size_mb=1024.0)
        self.mgr.unpin_kv_slot("sess_2")

        # Create session 3 with large KV size (1500 MB) pushing over 80% watermark (8192 * 0.80 = 6553.6 MB)
        s3 = self.mgr.allocate_or_update_kv_slot("sess_3", "mios-opencode", token_count=4000, size_mb=1500.0)

        status = self.mgr.get_status()
        self.assertGreaterEqual(status["kv_in_host"], 1)
        self.assertEqual(s1.location, "host_ram")  # s1 was oldest inactive, should be paged out

    def test_pinned_active_session_protection(self):
        """Test that actively generating session slots are never paged out."""
        self.mgr.activate_model("mios-opencode")
        s_active = self.mgr.allocate_or_update_kv_slot("active_session", "mios-opencode", token_count=100, size_mb=1024.0)
        self.assertTrue(s_active.is_pinned)

        # Trigger page out attempt
        paged = self.mgr._page_out_oldest_inactive_kv()
        self.assertFalse(paged)
        self.assertEqual(s_active.location, "vram")

    def test_session_state_preservation_on_page_in(self):
        """Test 100% token state preservation when paged-out session is recalled."""
        s = self.mgr.allocate_or_update_kv_slot("sess_persist", "mios-chat", token_count=3500, size_mb=512.0)
        self.mgr.unpin_kv_slot("sess_persist")
        s.location = "host_ram"

        ok, lat = self.mgr.page_in_kv_slot("sess_persist")
        self.assertTrue(ok)
        self.assertEqual(s.location, "vram")
        self.assertEqual(s.token_count, 3500)
        self.assertLess(lat, MAX_SWAP_LATENCY_MS)


# ===== merged from tests/test-ai.py (prefix vw_) =====
"""Automated tests for WS-AI GPU memory evaluation and watermark threshold breaches."""


import importlib.util
import os
import sys
import unittest

vw__HERE = os.path.dirname(os.path.abspath(__file__))
vw__ROOT = os.path.normpath(os.path.join(vw__HERE, ".."))
vw__VRAM_PATH = os.path.join(vw__ROOT, "usr", "libexec", "mios", "ai", "vram-watchdog.py")

vw_spec = importlib.util.spec_from_file_location("vram_watchdog", vw__VRAM_PATH)
if vw_spec and vw_spec.loader:
    vw_vram_watchdog = importlib.util.module_from_spec(vw_spec)
    sys.modules[vw_spec.name] = vw_vram_watchdog
    vw_spec.loader.exec_module(vw_vram_watchdog)
else:
    raise ImportError(f"Could not load vram-watchdog module from {vw__VRAM_PATH}")

class vw_TestVramWatchdog(unittest.TestCase):
    """Validates VRAM ratio computation and eviction decisions."""

    def test_vram_watermark_evaluation(self):
        monitor = vw_vram_watchdog.VramMonitor(watermark_threshold=0.95)

        # 90% utilization (below 95%)
        needs_evict, ratio = monitor.evaluate_vram_status(used_bytes=90, total_bytes=100)
        self.assertFalse(needs_evict)
        self.assertAlmostEqual(ratio, 0.90)

        # 96% utilization (above 95%)
        needs_evict, ratio = monitor.evaluate_vram_status(used_bytes=96, total_bytes=100)
        self.assertTrue(needs_evict)
        self.assertAlmostEqual(ratio, 0.96)

def vw_main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(vw_TestVramWatchdog)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


def main() -> int:
    rc = 0 if unittest.main(argv=[sys.argv[0]], exit=False).result.wasSuccessful() else 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
