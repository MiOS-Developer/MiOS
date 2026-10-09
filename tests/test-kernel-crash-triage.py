#!/usr/bin/env python3
# AI-hint: Automated unit test suite for Kernel Crash Dump Triage Engine (task_585, task_586).
# AI-related: usr/libexec/mios/kernel/crash_triage.py, tests/test-kernel-crash-triage.py
"""Automated unit test suite for MiOS Kernel Crash Dump Triage Engine."""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "usr", "libexec", "mios", "kernel"))

from crash_triage import KernelCrashTriageEngine, CrashReport

class TestKernelCrashTriage(unittest.TestCase):
    def setUp(self):
        self.engine = KernelCrashTriageEngine(dry_run=True)

    def test_vmcore_stack_extraction_and_ticket_gen(self):
        """Test parsing panic vmcore extracts faulting module and callstack."""
        rep = self.engine.triage_vmcore("/var/crash/vmcore.zst", mock_panic="Null pointer dereference in nvidia")
        self.assertEqual(rep.faulting_module, "nvidia_modeset")
        self.assertEqual(rep.isolation_category, "proprietary_gpu")
        self.assertGreaterEqual(len(rep.callstack), 2)
        self.assertIsNotNone(rep.ticket_id)

    def test_dmesg_oops_parsing(self):
        """Test parsing kernel oops output with registers and call trace."""
        oops_sample = """
BUG: unable to handle page fault for address 0000000000000010
#PF: supervisor read access in kernel mode
#PF: error_code(0x0000) - not-present page
RIP: 0010:bch2_btree_node_read+0x42/0x180 [bcachefs]
RSP: 0018:ffffc90001857bc8 EFLAGS: 00010246
RAX: 0000000000000000 RBX: ffff888102345000 RCX: 0000000000000001
CR2: 0000000000000010
Call Trace:
 <TASK>
 bch2_btree_node_iter_init+0x1a/0x50 [bcachefs]
 bch2_trans_begin+0x55/0x90 [bcachefs]
 sys_read+0x102/0x150
 </TASK>
        """
        rep = self.engine.parse_dmesg_oops(oops_sample)
        self.assertEqual(rep.faulting_module, "bcachefs")
        self.assertEqual(rep.isolation_category, "filesystem")
        self.assertIn("RIP", rep.registers)
        self.assertIn("CR2", rep.registers)
        self.assertEqual(rep.registers["CR2"], "0000000000000010")
        self.assertGreater(len(rep.callstack), 1)

    def test_dmesg_empty_rsp_handling(self):
        """Test parsing oops block with empty or bare RSP line does not crash."""
        oops_sample = """
BUG: unable to handle page fault
RIP: 0010:vmlinux_crash+0x10/0x20
RSP:
CR2: 0000000000000000
        """
        rep = self.engine.parse_dmesg_oops(oops_sample)
        self.assertNotIn("RSP", rep.registers)
        self.assertEqual(rep.registers["CR2"], "0000000000000000")

    def test_rust_symbol_demangling(self):
        """Test demangling Rust symbol names into structured module paths."""
        mangled = "_RNvNtCs1234_4core3fmt10write_char"
        demangled = self.engine.demangle_symbol(mangled)
        self.assertIn("core::fmt::write_char", demangled)

    def test_markdown_and_postgres_ticket_schema(self):
        """Test formatting creates valid markdown report and PostgreSQL ticket record."""
        rep = self.engine.triage_vmcore("/var/crash/vmcore.zst")
        md = self.engine.format_markdown_report(rep)
        self.assertIn("### Kernel Crash Report", md)
        self.assertIn(rep.faulting_module, md)

        ticket = self.engine.generate_postgres_ticket(rep)
        self.assertEqual(ticket["ticket_id"], rep.ticket_id)
        self.assertEqual(ticket["module"], rep.faulting_module)
        self.assertEqual(ticket["status"], "OPEN")
        parsed_stack = json.loads(ticket["callstack_json"])
        self.assertIsInstance(parsed_stack, list)

if __name__ == "__main__":
    unittest.main()
