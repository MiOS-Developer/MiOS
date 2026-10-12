#!/usr/bin/env python3
# AI-hint: Verify actual monitor lifecycle restores caller signal handlers before launching another terminal client, including real Linux Textual stale-handler failure.
# AI-related: usr/libexec/mios/mios-mon.py
import ast
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

if __name__ == '__main__':
    unittest.main()
