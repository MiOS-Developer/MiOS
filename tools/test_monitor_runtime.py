#!/usr/bin/env python3
# AI-hint: Verify actual monitor lifecycle restores caller signal handlers before launching another terminal client, including real Linux Textual stale-handler failure.
# AI-related: usr/libexec/mios/mios-mon.py
import ast
import json
import os
import shutil
import tempfile
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

def load_runner(env):
    source = ROOT / 'usr/libexec/mios/mios-mon.py'
    function = next(n for n in ast.parse(source.read_text(encoding='utf-8')).body
                    if isinstance(n, ast.FunctionDef) and n.name == '_run_monitor_app')
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), 'exec'), env)
    return env['_run_monitor_app']

class Signals:
    SIGINT, SIGTERM, SIGCONT = 2, 15, 18
    def __init__(self):
        self.handlers = {2: object(), 15: object(), 18: object()}
    def getsignal(self, number):
        return self.handlers[number]
    def signal(self, number, handler):
        self.handlers[number] = handler

class TestMonitorLifecycle(unittest.TestCase):
    def test_restores_handlers_after_selection_and_failed_constructor(self):
        for fail in (None, "constructor", "run"):
            signals = Signals()
            previous = dict(signals.handlers)
            class App:
                def __init__(self, **kwargs):
                    signals.signal(signals.SIGCONT, object())
                    if fail == "constructor":
                        raise RuntimeError('constructor failed')
                def run(self):
                    signals.signal(signals.SIGINT, object())
                    if fail == 'run':
                        raise RuntimeError('run failed')
                    return 'opencode'
            run = load_runner(dict(signal=signals, MiosMonitorApp=App))
            if fail:
                with self.assertRaisesRegex(RuntimeError, fail + ' failed'):
                    run(ui_mode='clients')
            else:
                self.assertEqual(run(ui_mode='clients'), 'opencode')
            self.assertEqual(signals.handlers, previous)

    @unittest.skipUnless(sys.platform.startswith('linux'), 'Linux Textual signal regression')
    def test_real_textual_resume_callback_is_removed_before_child_execution(self):
        probe = r"""
import os, signal
from textual.drivers.linux_driver import LinuxDriver
from test_monitor_runtime import load_runner
calls=[]
original=signal.getsignal(signal.SIGCONT)
def prior(*args): calls.append(True)
signal.signal(signal.SIGCONT,prior)
class App:
 def __init__(self,**kwargs):
  self.driver=LinuxDriver.__new__(LinuxDriver)
  self.driver._auto_restart=True
  self.driver.fileno=0
 def run(self):
  signal.signal(signal.SIGCONT,self.driver._sigcont_application)
  return 'opencode'
try:
 run=load_runner(dict(signal=signal,MiosMonitorApp=App))
 assert run()=='opencode'
 os.kill(os.getpid(),signal.SIGCONT)
 assert calls==[True]
 # Negative control: the real stale driver callback reproduces the report.
 App().run()
 try: os.kill(os.getpid(),signal.SIGCONT)
 except RuntimeError as error: assert 'no running event loop' in str(error)
 else: raise AssertionError('stale Textual handler did not reproduce')
finally: signal.signal(signal.SIGCONT,original)
"""
        subprocess.run([sys.executable, '-c', probe], cwd=ROOT / 'tools',
                       check=True, timeout=15, capture_output=True, text=True)


class TestMonitorIdentity(unittest.TestCase):
    def test_footer_never_opens_credential_files(self):
        from unittest import mock
        source = ROOT / "usr/libexec/mios/mios-mon.py"
        node = next(n for n in ast.parse(source.read_text()).body
                    if isinstance(n, ast.FunctionDef) and n.name == "get_credentials_text")
        env = {"monitor_config": lambda: {"identity": {"username": "operator"}},
               "escape": str}
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), "exec"), env)
        with mock.patch("builtins.open", side_effect=AssertionError("credential file read")):
            self.assertEqual(env["get_credentials_text"](), "[dim]login[/] [cyan]operator[/]")


class TestDashboardEntry(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mios-dashboard-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.helper = self.root / "usr/libexec/mios"
        self.helper.mkdir(parents=True)
        self.modules = self.root / "usr/lib/mios"
        self.modules.mkdir(parents=True)
        self.native = self.root / "bin"
        self.native.mkdir()
        self.marker = self.root / "delegate.json"
        self.entry = self.helper / "mios-dashboard"
        source = Path(globals().get("_DASHBOARD_UNDER_TEST",
                                   ROOT / "usr/libexec/mios/mios-dashboard"))
        shutil.copyfile(source, self.entry)
        self.entry.chmod(0o755)
        for name in ("rich", "textual", "psutil", "mios_toml", "mios_agent_tui"):
            (self.modules / (name + ".py")).write_text("# private dependency fixture\n")
        for path, delegate in ((self.native / "mios-gen", "native"),
                               (self.helper / "mios-mon.py", "interactive")):
            script = ("#!/usr/bin/env python3\nimport json, pathlib, sys\n"
                      f"result = {{'delegate': {delegate!r}, 'argv': sys.argv[1:]}}\n"
                      f"pathlib.Path({str(self.marker)!r}).write_text(json.dumps(result))\n"
                      "print(json.dumps(result))\n")
            path.write_text(script)
            path.chmod(0o755)
        self.env = dict(os.environ, MIOS_NATIVE_BIN_DIR=str(self.native),
                        MIOS_ROOT=str(self.root), PYTHONPATH=str(self.modules))

    def call(self, *args, tty=False):
        if not tty:
            return subprocess.run([str(self.entry), *args], env=self.env,
                                  text=True, capture_output=True, timeout=3)
        import pty
        master, slave = pty.openpty()
        try:
            process = subprocess.Popen([str(self.entry), *args], env=self.env,
                                       stdin=slave, stdout=slave, stderr=subprocess.PIPE)
            _, error = process.communicate(timeout=3)
            return subprocess.CompletedProcess(process.args, process.returncode,
                                               "", error.decode())
        finally:
            os.close(master)
            os.close(slave)

    def test_snapshot_uses_native_root_and_forwards_args(self):
        result = self.call("--once", "--width", "47", "--no-probe")
        self.assertEqual(result.returncode, 0, result.stderr)
        delegated = json.loads(self.marker.read_text())
        self.assertEqual(delegated, {"delegate": "native", "argv": [
            "dashboard", "--root", str(self.root), "--width", "47", "--no-probe"]})

    def test_nonterminal_monitor_is_one_native_snapshot(self):
        result = self.call("--monitor")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(result.stdout.splitlines()), 1)
        self.assertEqual(json.loads(self.marker.read_text())["delegate"], "native")

    def test_missing_native_engine_fails_without_legacy_roster(self):
        (self.native / "mios-gen").unlink()
        result = self.call("--once")
        self.assertEqual(result.returncode, 127)
        self.assertIn("Required native dashboard", result.stderr)
        self.assertFalse(self.marker.exists())

    @unittest.skipUnless(sys.platform.startswith("linux"), "private Linux PTY")
    def test_interactive_monitor_uses_existing_canonical_tui(self):
        result = self.call("--monitor", tty=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.marker.read_text()),
                         {"delegate": "interactive", "argv": ["--monitor"]})

    @unittest.skipUnless(sys.platform.startswith("linux"), "private Linux PTY")
    def test_once_overrides_monitor_even_in_a_terminal(self):
        result = self.call("--monitor", "--once", tty=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.marker.read_text())["delegate"], "native")

    @unittest.skipUnless(sys.platform.startswith("linux"), "private Linux PTY")
    def test_missing_interactive_dependency_fails_before_launch(self):
        (self.modules / "rich.py").write_text("raise ImportError('private missing dependency')\n")
        result = self.call("--monitor", tty=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Required monitor dependencies", result.stderr)
        self.assertFalse(self.marker.exists())


if __name__ == '__main__':
    unittest.main()
