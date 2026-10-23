#!/usr/bin/env python3
# AI-hint: mios-mon service dots read the procfs LISTEN table instead of connecting to every [ports] service each refresh (a connect probe made xrdp fork and log a failed handshake every 3 s).
# AI-related: usr/libexec/mios/mios-mon.py
import ast
import os
import socket
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / 'usr/libexec/mios/mios-mon.py'


def load(env, *names):
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert {n.name for n in body} == set(names), [n.name for n in body]
    exec(compile(ast.Module(body=body, type_ignores=[]), str(SOURCE), 'exec'), env)
    return [env[n] for n in names]


class NoConnect:
    """A socket module whose connect fails the test: local checks must not open a connection."""
    def __init__(self):
        self.calls = []

    def create_connection(self, address, timeout=None):
        self.calls.append(address)
        raise ConnectionRefusedError(address)


HEADER = '  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n'


class TestListeningPorts(unittest.TestCase):
    def setUp(self):
        self.fake = NoConnect()
        self.listening, self.check = load({'socket': self.fake}, 'listening_tcp_ports', 'check_port')

    def tables(self, v4, v6):
        d = tempfile.mkdtemp(prefix='mon-ports-')
        self.addCleanup(lambda: [os.unlink(os.path.join(d, f)) for f in os.listdir(d)] and os.rmdir(d))
        paths = []
        for name, rows in (('tcp', v4), ('tcp6', v6)):
            path = os.path.join(d, name)
            with open(path, 'w', encoding='ascii') as fh:
                fh.write(HEADER + ''.join(rows))
            paths.append(path)
        return tuple(paths)

    def test_parses_listen_rows_only(self):
        v4 = ['   0: 00000000:206C 00000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 1 1\n',
              '   1: 0100007F:1F90 0100007F:9C40 01 00000000:00000000 00:00000000 00000000  1000        0 2 1\n']
        v6 = ['   0: 00000000000000000000000000000000:0016 00000000000000000000000000000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 3 1\n']
        self.assertEqual(self.listening(self.tables(v4, v6)), {8300, 22})

    def test_unavailable_procfs_is_unknown(self):
        self.assertIsNone(self.listening(('/nonexistent/tcp', '/nonexistent/tcp6')))

    def test_local_checks_never_connect(self):
        self.assertTrue(self.check('127.0.0.1', 8300, {8300, 22}))
        self.assertFalse(self.check('127.0.0.1', 8400, {8300, 22}))
        self.assertFalse(self.check('127.0.0.1', 0, {8300}))
        self.assertEqual(self.fake.calls, [])

    def test_negative_control_without_table_still_probes(self):
        # The pre-fix path: no table means a connect probe (here refused) -- what made xrdp log every 3 s.
        self.assertFalse(self.check('127.0.0.1', 8300))
        self.assertTrue(self.fake.calls, 'expected the connect fallback to run')

    @unittest.skipUnless(os.path.exists('/proc/net/tcp'), 'Linux procfs needed')
    def test_real_listener_appears_and_disappears(self):
        listening, = load({'socket': socket}, 'listening_tcp_ports')
        srv = socket.socket()
        srv.bind(('127.0.0.1', 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        try:
            self.assertIn(port, listening())
        finally:
            srv.close()
        self.assertNotIn(port, listening())


if __name__ == '__main__':
    unittest.main()
