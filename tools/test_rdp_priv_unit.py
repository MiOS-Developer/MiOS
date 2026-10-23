#!/usr/bin/env python3
# AI-hint: mios-rdp-priv@.service must keep CAP_SYS_PTRACE in its bounding set (TLS-KEY reads the caller's /proc/<pid>/exe, owned by the operator) while ptrace(2) stays outside its syscall filter.
# AI-related: usr/lib/systemd/system/mios-rdp-priv@.service, src/mios-rdp-server/crates/mios-rdp-priv/src/main.rs
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UNIT = ROOT / 'usr/lib/systemd/system/mios-rdp-priv@.service'


def directives(text):
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith(('#', ';', '[')) and '=' in line:
            key, value = line.split('=', 1)
            out.setdefault(key.strip(), []).append(value.strip())
    return out


class TestPrivUnit(unittest.TestCase):
    def setUp(self):
        self.d = directives(UNIT.read_text(encoding='utf-8'))

    def test_bounding_set_allows_reading_the_callers_exe(self):
        caps = ' '.join(self.d.get('CapabilityBoundingSet', [])).split()
        self.assertIn('CAP_SYS_PTRACE', caps, 'TLS-KEY cannot read /proc/<pid>/exe of the operator-owned server')
        for cap in ('CAP_DAC_READ_SEARCH', 'CAP_SETUID', 'CAP_SETGID', 'CAP_AUDIT_WRITE'):
            self.assertIn(cap, caps)

    def test_ptrace_syscall_stays_filtered(self):
        filters = self.d.get('SystemCallFilter', [])
        self.assertEqual(filters[0], '@system-service')
        allowed = ' '.join(f for f in filters if not f.startswith('~')).split()
        denied = ' '.join(f[1:] for f in filters if f.startswith('~')).split()
        self.assertNotIn('@debug', allowed)
        self.assertNotIn('ptrace', allowed)
        # CAP_SYS_PTRACE would let process_vm_readv/writev (in @ipc) touch other processes' memory.
        self.assertIn('process_vm_readv', denied)
        self.assertIn('process_vm_writev', denied)
        self.assertIn('yes', self.d.get('NoNewPrivileges', []))

    @unittest.skipUnless(shutil.which('systemd-analyze'), 'systemd-analyze needed')
    def test_effective_filter_excludes_memory_access(self):
        def expand(name, seen=None):
            seen = set() if seen is None else seen
            if not name.startswith('@'):
                return {name}
            if name in seen:
                return set()
            seen.add(name)
            out = subprocess.run(['systemd-analyze', 'syscall-filter', name],
                                 capture_output=True, text=True, timeout=30)
            if out.returncode != 0:
                self.skipTest(out.stderr.strip()[:200])
            calls = set()
            for line in out.stdout.splitlines()[1:]:
                word = line.strip()
                if word and not word.startswith('#'):
                    calls |= expand(word, seen)
            return calls
        filters = self.d.get('SystemCallFilter', [])
        allowed = set()
        for f in filters:
            if not f.startswith('~'):
                for word in f.split():
                    allowed |= expand(word)
        for f in filters:
            if f.startswith('~'):
                for word in f[1:].split():
                    allowed -= expand(word)
        self.assertIn('read', allowed)        # positive control: the expansion is real
        self.assertIn('sendmsg', allowed)     # the helper's own socket I/O stays allowed
        for call in ('ptrace', 'pidfd_getfd', 'process_vm_readv', 'process_vm_writev'):
            self.assertNotIn(call, allowed)


if __name__ == '__main__':
    unittest.main()
