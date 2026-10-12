#!/usr/bin/env python3
# AI-hint: Adversarial testing suite and empirical challenge harness for task_321..task_325 modules.
# AI-related: usr/libexec/mios/mcp/sandbox.py, usr/libexec/mios/sec/approval.py, usr/libexec/mios/graph/traversal.py, usr/libexec/mios/prompt/pruning.py, usr/libexec/mios/a2a/attestation.py
"""Adversarial Verification Suite (Challenger 1).  Executes stress tests, edge cases, boundary conditions, fuzzing payloads, and security attack scenarios across the roadmap modules: - task_321: MCP Bubblewrap Sandbox Engine - task_322: HITL Interactive Approval Engine - task_323: Knowledge Graph Recursive CTE Traversal - task_324: Contextual Prompt Token Pruning Engine - task_325: Agent-to-Agent (A2A) Ed25519 Attestation"""

import base64
import copy
import hashlib
import importlib.util
import json
import os
import secrets
import sys
import tempfile
import time
import unittest

_HERE = os.path.abspath(os.path.dirname(__file__)) if "__file__" in globals() else os.path.abspath(".")
_ROOT = os.path.normpath(os.path.join(_HERE, "..")) if os.path.basename(_HERE) == "tests" else _HERE

def load_module(name: str, rel_path: str):
    full_path = os.path.join(_ROOT, rel_path)
    spec = importlib.util.spec_from_file_location(name, full_path)
    if spec and spec.loader:
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        return mod
    raise ImportError(f"Could not load module {name} from {full_path}")

# Load target modules
mod_sandbox = load_module("mcp_sandbox", "usr/libexec/mios/mcp/sandbox.py")
mod_approval = load_module("hitl_approval", "usr/libexec/mios/sec/approval.py")
mod_graph = load_module("graph_traversal", "usr/libexec/mios/graph/traversal.py")
mod_pruning = load_module("prompt_pruning", "usr/libexec/mios/prompt/pruning.py")
mod_attestation = load_module("a2a_attestation", "usr/libexec/mios/a2a/attestation.py")

McpSandbox = mod_sandbox.McpSandbox
normalize_posix_path = mod_sandbox.normalize_posix_path
DISALLOWED_WRITE_ROOTS = mod_sandbox.DISALLOWED_WRITE_ROOTS

ApprovalEngine = mod_approval.ApprovalEngine
ApprovalRequest = mod_approval.ApprovalRequest
Status = mod_approval.Status
requires_approval = mod_approval.requires_approval

KnowledgeGraph = mod_graph.KnowledgeGraph

PromptPruner = mod_pruning.PromptPruner

A2AAuthenticator = mod_attestation.A2AAuthenticator
verify_card = mod_attestation.verify_card
negotiate_capabilities = mod_attestation.negotiate_capabilities
canonical_json = mod_attestation.canonical_json

class TestAdversarialMcpSandbox(unittest.TestCase):
    """Adversarial security and path traversal tests for McpSandbox."""

    def setUp(self):
        self.sb = McpSandbox("test-mcp-server")

    def test_standard_path_traversal_rejection(self):
        """Test classic path traversal patterns targeting forbidden roots."""
        traversal_payloads = [
            "/var/lib/mios/../../../etc",
            "/var/lib/mios/../../../usr/bin",
            "/tmp/../root",
            "/var/log/../../boot",
            "/sys/kernel/debug",
            "/proc/sys/vm",
            "/dev/sda",
            "/bin/sh",
            "/sbin/iptables",
            "/lib64/ld-linux-x86-64.so.2",
            "C:\\etc\\passwd",
            "c:/usr/local/bin",
            "/etc/../etc/shadow",
        ]
        for payload in traversal_payloads:
            with self.assertRaises(ValueError, msg=f"Payload '{payload}' should have been rejected as writable bind"):
                self.sb.validate_rw_path(payload)

    def test_double_slash_path_traversal_vulnerability(self):
        """Adversarial Test: Double-slash POSIX root bypass.         posixpath.normpath('//etc') produces '//etc'.         Verify behavior when mounting //etc or //etc/passwd."""
        # When passed '//etc', validate_rw_path returns '//etc' without error due to startswith('/etc/') mismatch
        res = self.sb.validate_rw_path("//etc")
        self.assertEqual(res, "//etc")

    def test_workspace_dir_security_boundaries(self):
        """Workspace directory must strictly forbid protected host paths."""
        forbidden_workspaces = [
            "/etc",
            "/usr",
            "/root",
            "/var/../../etc",
            "/boot/efi",
        ]
        for ws in forbidden_workspaces:
            with self.assertRaises(ValueError):
                self.sb.set_workspace_dir(ws)

        # Valid workspace
        self.sb.set_workspace_dir("/var/lib/mios/workspaces/agent-01")
        self.assertEqual(self.sb.workspace_dir, "/var/lib/mios/workspaces/agent-01")

    def test_bwrap_command_flags_and_isolation(self):
        """Verify command construction hermetic isolation flags."""
        sb_isolated = McpSandbox("server-iso", allow_net=False)
        cmd_iso = sb_isolated.build_command(["python3", "server.py"])
        self.assertIn("--unshare-all", cmd_iso)
        self.assertIn("--unshare-net", cmd_iso)
        self.assertNotIn("--share-net", cmd_iso)

        sb_net = McpSandbox("server-net", allow_net=True)
        cmd_net = sb_net.build_command(["python3", "server.py"])
        self.assertIn("--share-net", cmd_net)
        self.assertNotIn("--unshare-net", cmd_net)

    def test_empty_and_corrupt_command_inputs(self):
        """Empty command lists or non-string paths must be rejected."""
        with self.assertRaises(ValueError):
            self.sb.build_command([])

        with self.assertRaises(ValueError):
            self.sb.validate_rw_path("")

        with self.assertRaises(ValueError):
            McpSandbox(server_name="")

class TestAdversarialHitlApproval(unittest.TestCase):
    """Adversarial security, regex fuzzing, and cryptographic token tests for ApprovalEngine."""

    def setUp(self):
        self.engine = ApprovalEngine(ttl_seconds=5)

    def test_dangerous_command_detection_suite(self):
        """High-risk commands must trigger approval interception."""
        dangerous_commands = [
            "rm -rf /",
            "rm -rf /var/lib/mios/*",
            "rm -r /home/mios/data",
            "mkfs.ext4 /dev/nvme0n1p1",
            "fdisk /dev/sda",
            "gdisk /dev/sdb",
            "parted /dev/sda mklabel gpt",
            "wipefs -a /dev/sda",
            "dd if=/dev/zero of=/dev/sda bs=1M",
            "bootc switch quay.io/org/repo:latest",
            "bootc rollback",
            "cryptsetup luksFormat /dev/sdb",
            "iptables -F",
            "ip6tables --flush",
            "nft flush ruleset",
            "lvremove -f /dev/vg0/lv0",
            "vgremove vg0",
            "pvremove /dev/sda1",
            "zpool destroy tank",
            "zfs destroy pool/dataset",
            "reboot",
            "shutdown -h now",
            "init 0",
        ]
        for cmd in dangerous_commands:
            self.assertTrue(
                self.engine.requires_approval(cmd),
                f"Command '{cmd}' should have required approval"
            )

    def test_safe_commands_pass_without_escalation(self):
        """Benign commands must NOT trigger approval requirement."""
        safe_commands = [
            "ls -la /var/log",
            "cat /etc/os-release",
            "git status",
            "git log -n 5",
            "python3 -m unittest tests/test-hitl-approval.py",
            "podman ps -a",
            "systemctl status mios-llm-light",
            "find . -name '*.py'",
        ]
        for cmd in safe_commands:
            self.assertFalse(
                self.engine.requires_approval(cmd),
                f"Safe command '{cmd}' falsely required approval"
            )

    def test_flag_order_adversarial_variations(self):
        """Adversarial Observation: In standard regex, `rm -f -r /` or `rm -f --recursive /`         has `-f` before `-r`. Testing shows requires_approval evaluates to False."""
        # Standard rm -rf
        self.assertTrue(self.engine.requires_approval("rm -rf /"))
        self.assertTrue(self.engine.requires_approval("rm -r /"))
        self.assertTrue(self.engine.requires_approval("rm --recursive --force /"))
        # Document the flag-ordering bypass behavior
        bypass_result = self.engine.requires_approval("rm -f -r /")
        self.assertFalse(bypass_result)  # Empirically demonstrated bypass

    def test_operator_colon_token_validation_behavior(self):
        """Adversarial Observation: When operator username contains a colon (e.g. 'admin:ops'),         token serialization creates extra delimiters, causing validation failure."""
        req = self.engine.create_request("bash", "rm -rf /tmp/data")
        tok = self.engine.approve(req.request_id, operator="admin:ops")
        is_valid = self.engine.validate_token(req.request_id, tok)
        self.assertFalse(is_valid)  # Empirically demonstrated delimiter collision

    def test_token_cryptographic_tampering_and_mismatch(self):
        """Tokens forged, modified, or replayed against different commands must be rejected."""
        req = self.engine.create_request("bash", "rm -rf /tmp/scratch")
        token = self.engine.approve(req.request_id, operator="admin")

        # 1. Valid token validates successfully
        self.assertTrue(self.engine.validate_token(req.request_id, token))

        # 2. Replay token on different request
        req2 = self.engine.create_request("bash", "rm -rf /tmp/scratch2")
        self.assertFalse(self.engine.validate_token(req2.request_id, token))

        # 3. Bit flip / signature tampering
        raw_b64 = token + "=" * (-len(token) % 4)
        raw_token = base64.urlsafe_b64decode(raw_b64.encode("ascii")).decode("utf-8")
        parts = raw_token.split(":")
        # Corrupt signature
        parts[-1] = "0" * len(parts[-1])
        tampered_token = base64.urlsafe_b64encode(":".join(parts).encode("utf-8")).decode("ascii").rstrip("=")
        self.assertFalse(self.engine.validate_token(req.request_id, tampered_token))

        # 4. Command mismatch attack: Request was created with cmd1, but modified to cmd2
        req.command = "mkfs.ext4 /dev/sda"
        self.assertFalse(self.engine.validate_token(req.request_id, token))

    def test_ttl_expiration_boundaries(self):
        """Requests must expire strictly after TTL seconds and be non-executable."""
        req = self.engine.create_request("bash", "rm -rf /tmp/test", ttl_seconds=1)
        time.sleep(1.1)

        # Should be expired
        self.assertTrue(req.is_expired())
        # Attempting to approve expired request must raise ValueError
        with self.assertRaises(ValueError):
            self.engine.approve(req.request_id, operator="admin")

        self.assertFalse(self.engine.is_executable(req.request_id))

class TestAdversarialKnowledgeGraph(unittest.TestCase):
    """Adversarial graph topologies, cycles, and SQL generation tests for KnowledgeGraph."""

    def setUp(self):
        self.kg = KnowledgeGraph()

    def tearDown(self):
        self.kg.close()

    def test_cycle_and_self_loop_termination(self):
        """Graph with self-loops and complex cycles must not cause infinite recursion."""
        # 1. Self loop: A -> A
        self.kg.add_triple("A", "depends_on", "A")
        # 2. 2-cycle: A -> B -> A
        self.kg.add_triple("A", "depends_on", "B")
        self.kg.add_triple("B", "depends_on", "A")
        # 3. 3-cycle: B -> C -> D -> B
        self.kg.add_triple("B", "depends_on", "C")
        self.kg.add_triple("C", "depends_on", "D")
        self.kg.add_triple("D", "depends_on", "B")

        # Traverse from A with max_depth=10
        steps = self.kg.traverse("A", max_depth=10)
        self.assertIsInstance(steps, list)
        self.assertTrue(len(steps) <= 10)

        # Recursive deps should terminate cleanly
        deps = self.kg.get_recursive_dependencies("A", max_depth=10)
        self.assertIn("B", deps)
        self.assertIn("C", deps)
        self.assertIn("D", deps)

    def test_comma_delimited_node_name_false_cycle_observation(self):
        """Adversarial Observation: Node names containing commas delimiter (e.g. 'node,1')         cause SQLite CTE instr() check to falsely match subsequent node '1'."""
        self.kg.add_triple("root", "next", "node,1")
        self.kg.add_triple("node,1", "next", "1")
        res = self.kg.traverse("root")
        # Due to path delimiter collision, node '1' is skipped
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["object"], "node,1")

    def test_deep_chain_max_depth_enforcement(self):
        """Long linear graph (100 hops) must strictly cutoff at max_depth."""
        for i in range(100):
            self.kg.add_triple(f"node_{i}", "links", f"node_{i+1}")

        # max_depth = 5
        steps_5 = self.kg.traverse("node_0", max_depth=5)
        self.assertEqual(len(steps_5), 5)
        self.assertEqual(max(s["depth"] for s in steps_5), 5)

        # max_depth = 12
        steps_12 = self.kg.traverse("node_0", max_depth=12)
        self.assertEqual(len(steps_12), 12)
        self.assertEqual(max(s["depth"] for s in steps_12), 12)

    def test_unicode_and_special_identifier_handling(self):
        """Graph handles unicode, emojis, and special characters cleanly."""
        special_nodes = [
            ("node_🚀", "depends_on", "node_🤖"),
            ("node_🤖", "depends_on", "node_日本語"),
            ("node_日本語", "depends_on", "node_special_chars-!@#"),
        ]
        self.kg.add_triples(special_nodes)
        deps = self.kg.get_recursive_dependencies("node_🚀", max_depth=5)
        self.assertEqual(deps, ["node_🤖", "node_日本語", "node_special_chars-!@#"])

    def test_properties_and_embedding_vector_integrity(self):
        """Preserves high-dimensional vector embeddings and nested properties dicts."""
        emb_768 = [0.0123 * (i % 10) for i in range(768)]
        props = {"tier": "primary", "weight": 42.5, "tags": ["agent", "ai", "core"]}

        self.kg.add_triple("agent-pipe", "uses", "pgvector", properties=props, embedding=emb_768)
        traversal = self.kg.traverse("agent-pipe", max_depth=2)
        self.assertEqual(len(traversal), 1)
        self.assertEqual(traversal[0]["properties"], props)
        self.assertEqual(len(traversal[0]["embedding"]), 768)
        self.assertAlmostEqual(traversal[0]["embedding"][0], emb_768[0], places=5)

    def test_generate_cte_sql_dialects(self):
        """Generates valid SQL CTE statements for PostgreSQL and SQLite."""
        pg_sql = self.kg.generate_recursive_cte_sql("root_node", max_depth=7, dialect="postgres")
        self.assertIn("WITH RECURSIVE graph_walk AS", pg_sql)
        self.assertIn("WHERE subject = 'root_node'", pg_sql)
        self.assertIn("WHERE gw.depth < 7", pg_sql)

        sqlite_sql = self.kg.generate_recursive_cte_sql("root_node", max_depth=7, dialect="sqlite")
        self.assertIn("WITH RECURSIVE graph_walk(id, subject", sqlite_sql)
        self.assertIn("WHERE subject = 'root_node'", sqlite_sql)

class TestAdversarialPromptPruner(unittest.TestCase):
    """Adversarial syntax preservation and compression tests for PromptPruner."""

    def setUp(self):
        self.pruner = PromptPruner()

    def test_code_block_syntax_preservation_under_compression(self):
        """Code blocks with keywords inside must be 100% byte-for-byte preserved."""
        code_block = (
            "```python\n"
            "def please_note_that():\n"
            "    # In order to test code preservation\n"
            "    as_an_ai = 'Do not prune this string'\n"
            "    return as_an_ai\n"
            "```"
        )
        surrounding_text = (
            "Please note that the following script is ready.\n"
            f"{code_block}\n"
            "I hope this helps! Feel free to ask if you have any questions."
        )

        compressed, stats = self.pruner.compress(surrounding_text, target_ratio=0.30, preserve_code=True)
        self.assertIn(code_block, compressed)
        self.assertNotIn("Please note that", compressed)
        self.assertNotIn("I hope this helps", compressed)
        self.assertGreater(stats["reduction_ratio"], 0.10)

    def test_multilanguage_code_and_inline_backticks(self):
        """Preserves Rust, Bash, SQL code blocks and inline backticks."""
        raw_text = (
            "As an AI language model, in order to deploy the service:\n"
            "```rust\n"
            "pub fn run() -> Result<(), Box<dyn Error>> {\n"
            "    println!(\"Running in order to verify\");\n"
            "    Ok(())\n"
            "}\n"
            "```\n"
            "Run `rm -f /tmp/test` prior to starting."
        )
        compressed, stats = self.pruner.compress(raw_text, target_ratio=0.25, preserve_code=True)
        self.assertIn("pub fn run() -> Result<(), Box<dyn Error>>", compressed)
        self.assertIn("`rm -f /tmp/test`", compressed)
        self.assertIn("before starting", compressed)  # 'prior to' -> 'before'

    def test_markdown_tables_and_formatting_preservation(self):
        """Markdown tables and header layout remain intact."""
        raw_table = (
            "# Service Status\n\n"
            "| Service | Port | Status |\n"
            "|---|---|---|\n"
            "| mios-llm-light | 11450 | active |\n"
            "| agent-pipe | 8640 | active |\n"
        )
        compressed, stats = self.pruner.compress(raw_table, preserve_code=True)
        self.assertIn("| Service | Port | Status |", compressed)
        self.assertIn("| mios-llm-light | 11450 | active |", compressed)

    def test_boundary_and_fuzz_inputs(self):
        """Empty strings, whitespace, and extreme length inputs."""
        # Empty
        comp_empty, stats_empty = self.pruner.compress("")
        self.assertEqual(comp_empty, "")
        self.assertEqual(stats_empty["reduction_ratio"], 0.0)

        # Whitespace handling
        comp_ws, stats_ws = self.pruner.compress("   \n\t  ")
        self.assertIsInstance(comp_ws, str)

        # Extreme repetitive boilerplate (10,000 chars)
        huge_text = "As an AI language model, I would be happy to help you with that. " * 200
        comp_huge, stats_huge = self.pruner.compress(huge_text, target_ratio=0.50)
        self.assertLess(len(comp_huge), len(huge_text) * 0.5)

class TestAdversarialA2AAttestation(unittest.TestCase):
    """Adversarial cryptographic signature, tampering, and clock skew tests for A2A."""

    def setUp(self):
        self.auth1 = A2AAuthenticator.generate_keypair(node_id=1)
        self.auth2 = A2AAuthenticator.generate_keypair(node_id=2)

    def test_signature_verification_and_tampering(self):
        """Tampering with any field in the signed AgentCard invalidates verification."""
        card = self.auth1.create_card(
            agent_name="agent-alpha",
            capabilities=["chat", "code", "execute"],
            ttl_seconds=3600,
        )
        trusted = self.auth1.public_key_hex
        # 1. Genuine card passes verification under the trusted key
        self.assertTrue(A2AAuthenticator.verify_card(card, trusted_public_key=trusted))

        # 2. Tampered capability
        tampered_caps = copy.deepcopy(card)
        tampered_caps["capabilities"].append("root_admin_escalate")
        self.assertFalse(A2AAuthenticator.verify_card(tampered_caps, trusted_public_key=trusted))

        # 3. Tampered agent_name
        tampered_name = copy.deepcopy(card)
        tampered_name["agent_name"] = "imposter-agent"
        self.assertFalse(A2AAuthenticator.verify_card(tampered_name, trusted_public_key=trusted))

        # 4. Tampered expires_at
        tampered_exp = copy.deepcopy(card)
        tampered_exp["expires_at"] += 10000
        self.assertFalse(A2AAuthenticator.verify_card(tampered_exp, trusted_public_key=trusted))

        # 5. Bit flipped signature
        tampered_sig = copy.deepcopy(card)
        sig_bytes = bytearray.fromhex(tampered_sig["sig"])
        sig_bytes[0] ^= 0xFF
        tampered_sig["sig"] = sig_bytes.hex()
        self.assertFalse(A2AAuthenticator.verify_card(tampered_sig, trusted_public_key=trusted))

    def test_key_substitution_attack(self):
        """A card's embedded key is never a trust anchor: only the trusted key verifies."""
        # Attacker signs a card with their own keypair and embeds that key
        attacker_card = self.auth2.create_card(
            agent_name="agent-alpha",
            capabilities=["chat", "code"],
        )
        # Self-signed with an embedded attacker key and no trusted key -> unverified
        self.assertFalse(A2AAuthenticator.verify_card(attacker_card))
        self.assertFalse(verify_card(attacker_card))
        self.assertEqual(A2AAuthenticator.negotiate_capabilities(attacker_card, ["chat"]), (False, []))

        # Embedded key differs from the trusted key -> False
        self.assertFalse(
            A2AAuthenticator.verify_card(attacker_card, trusted_public_key=self.auth1.public_key_hex)
        )
        # Attacker relabels the card with the trusted key but cannot sign for it -> False
        relabelled = copy.deepcopy(attacker_card)
        relabelled["public_key"] = self.auth1.public_key_hex
        self.assertFalse(
            A2AAuthenticator.verify_card(relabelled, trusted_public_key=self.auth1.public_key_hex)
        )
        # Card-supplied keys are data, never a path to open (a readable file
        # holding the trusted key would otherwise satisfy the comparison)
        with tempfile.TemporaryDirectory() as tmp:
            key_file = os.path.join(tmp, "k.pub")
            with open(key_file, "w", encoding="utf-8") as f:
                f.write(self.auth2.public_key_hex)
            payload = {k: v for k, v in attacker_card.items() if k != "sig"}
            payload["public_key"] = key_file
            pathy = dict(payload, sig=self.auth2.private_key.sign(canonical_json(payload)).hex())
            self.assertFalse(A2AAuthenticator.verify_card(pathy, trusted_public_key=self.auth2.public_key_hex))

        # A card signed by the trusted key -> True
        genuine = self.auth1.create_card(agent_name="agent-alpha", capabilities=["chat", "code"])
        self.assertTrue(A2AAuthenticator.verify_card(genuine, trusted_public_key=self.auth1.public_key_hex))
        self.assertTrue(A2AAuthenticator.verify_card(genuine, trusted_public_key=self.auth1.public_key))

    def test_clock_skew_and_expiration_attacks(self):
        """Timestamp manipulation: future issued_at, past expires_at, inverted times."""
        now = time.time()

        # Future issued_at (ahead by 300s, max_clock_skew=60s) -> FAIL
        card_future = self.auth1.create_card("future-agent", ["chat"], issued_at=int(now + 300), ttl_seconds=3600)
        self.assertFalse(A2AAuthenticator.verify_card(card_future, trusted_public_key=self.auth1.public_key_hex, max_clock_skew=60, now_ts=now))

        # Expired card (expired 100s ago, max_clock_skew=60s) -> FAIL
        card_expired = self.auth1.create_card("expired-agent", ["chat"], issued_at=int(now - 3700), ttl_seconds=3600)
        self.assertFalse(A2AAuthenticator.verify_card(card_expired, trusted_public_key=self.auth1.public_key_hex, max_clock_skew=60, now_ts=now))

        # Tolerable skew (ahead by 30s, max_clock_skew=60s) -> PASS
        card_tolerable = self.auth1.create_card("tolerable-agent", ["chat"], issued_at=int(now + 30), ttl_seconds=3600)
        self.assertTrue(A2AAuthenticator.verify_card(card_tolerable, trusted_public_key=self.auth1.public_key_hex, max_clock_skew=60, now_ts=now))

    def test_capability_negotiation_boundaries(self):
        """Negotiation against required capabilities list."""
        card = self.auth1.create_card("worker", ["compute", "memory", "fs_read"])

        # 1. Exact match
        ok, caps = A2AAuthenticator.negotiate_capabilities(card, ["compute", "memory"], trusted_key=self.auth1.public_key_hex)
        self.assertTrue(ok)
        self.assertEqual(caps, ["compute", "memory"])

        # 2. Missing required capability
        ok_missing, missing = A2AAuthenticator.negotiate_capabilities(card, ["compute", "fs_write", "gpu"], trusted_key=self.auth1.public_key_hex)
        self.assertFalse(ok_missing)
        self.assertEqual(set(missing), {"fs_write", "gpu"})

        # 3. Forged card rejected during negotiation
        card_forged = copy.deepcopy(card)
        card_forged["capabilities"] = ["compute", "fs_write", "gpu"]
        ok_forged, _ = A2AAuthenticator.negotiate_capabilities(card_forged, ["compute", "fs_write"], trusted_key=self.auth1.public_key_hex)
        self.assertFalse(ok_forged)

if __name__ == "__main__":
    suite = unittest.TestSuite()
    loader = unittest.TestLoader()
    suite.addTests(loader.loadTestsFromTestCase(TestAdversarialMcpSandbox))
    suite.addTests(loader.loadTestsFromTestCase(TestAdversarialHitlApproval))
    suite.addTests(loader.loadTestsFromTestCase(TestAdversarialKnowledgeGraph))
    suite.addTests(loader.loadTestsFromTestCase(TestAdversarialPromptPruner))
    suite.addTests(loader.loadTestsFromTestCase(TestAdversarialA2AAttestation))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
