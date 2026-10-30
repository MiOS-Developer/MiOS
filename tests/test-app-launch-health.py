#!/usr/bin/env python3
# AI-hint: Two-sided compiled launcher checks with private document-portal buses/runtime dirs: mount recovery, concurrent coalescing, disabled recovery, sandbox errors, timeouts and invalid desktop IDs. Never restart the operator's portal.
# AI-related: tools/native/mios-gen/src/app_preflight.rs, tests/test-document-portal-isolation.py, usr/share/mios/quickshell/AppLaunch.qml
# AI-functions: LauncherHealth

import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time
import tomllib
import unittest

ROOT=Path(__file__).resolve().parents[1]
BINARY=os.environ.get('MIOS_APP_LAUNCH_BIN','/usr/bin/mios-gen')
LIVE=os.environ.get('MIOS_APP_LAUNCH_LIVE')=='1' and os.getuid()!=0

@unittest.skipUnless(LIVE,'opt in as the desktop operator with MIOS_APP_LAUNCH_LIVE=1')
class LauncherHealth(unittest.TestCase):
    def setUp(self):
        self.base=Path(tempfile.mkdtemp(prefix='mios-launch-',dir='/var/tmp'))
        self.runtime=self.base/'runtime';self.runtime.mkdir(mode=0o700)
        self.home=self.base/'home';self.home.mkdir()
        self.root=self.base/'root';(self.root/'usr/share/mios').mkdir(parents=True)
        self.calls=self.base/'recovery.calls';self.bus_pid=None
        self.env={k:v for k,v in os.environ.items() if k not in ('DBUS_SESSION_BUS_ADDRESS','DISPLAY','WAYLAND_DISPLAY') and not k.startswith('MIOS_')}
        self.env.update(HOME=str(self.home),XDG_CONFIG_HOME=str(self.home/'config'),XDG_DATA_HOME=str(self.home/'data'),XDG_RUNTIME_DIR=str(self.runtime))
        # Activation inherits this private runtime from the bus's birth. Starting
        # a private bus with the real user runtime can unmount the real portal.
        bus=subprocess.run(['dbus-daemon','--session','--fork','--print-address=1','--print-pid=1','--address=unix:path='+str(self.base/'bus')],env=self.env,capture_output=True,text=True,check=True,timeout=5)
        address,pid=bus.stdout.splitlines()[:2];self.bus_pid=int(pid);self.env['DBUS_SESSION_BUS_ADDRESS']=address
        self.policy=tomllib.loads((ROOT/'usr/share/mios/mios.toml').read_text())['desktop']['launch'].copy()
        self.policy['overall_timeout_ms']=5000;self.policy['probe_timeout_ms']=3000
        stub=self.base/'portal-restart'
        stub.write_text('''#!/usr/bin/python3
import json,os,subprocess,sys
from pathlib import Path
assert sys.argv[1:]==['--user','restart','xdg-document-portal.service']
with Path(%r).open('a') as f:f.write(json.dumps(sys.argv[1:])+'\\n')
subprocess.run(['gdbus','call','--session','--dest','org.freedesktop.portal.Documents','--object-path','/org/freedesktop/portal/documents','--method','org.freedesktop.portal.Documents.GetMountPoint'],check=True,timeout=2)
''' % str(self.calls));stub.chmod(0o755)
        self.policy['systemctl_program']=str(stub)
        self.save()

    def save(self):
        path=self.root/'usr/share/mios/mios.toml'
        path.write_text('[desktop.launch]\n'+''.join(k+' = '+json.dumps(v,ensure_ascii=False)+'\n' for k,v in self.policy.items()))

    def command(self,*args):return [BINARY,'app-preflight','--root',str(self.root),*args]
    def call(self,*args):return subprocess.run(self.command(*args),env=self.env,capture_output=True,text=True,timeout=8)

    def tearDown(self):
        if self.bus_pid:
            # Terminating the activating private bus ends only its own services.
            try:os.kill(self.bus_pid,signal.SIGTERM)
            except ProcessLookupError:pass
        mount=self.runtime/'doc'
        for _ in range(30):
            if not self.mounted(mount):break
            time.sleep(.1)
        if self.mounted(mount):subprocess.run(['fusermount3','-u','-z',str(mount)],capture_output=True,timeout=5)
        self.assertFalse(self.mounted(mount),'private test portal mount leaked')
        shutil.rmtree(self.base)

    @staticmethod
    def mounted(path):
        return any(line.split()[4]==str(path) and ' - fuse.portal ' in line for line in Path('/proc/self/mountinfo').read_text().splitlines())

    def test_real_private_portal_mount_is_recovered_once_for_concurrent_launches(self):
        self.assertFalse(self.mounted(self.runtime/'doc'))
        first=subprocess.Popen(self.command('--portal-only'),env=self.env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        second=subprocess.Popen(self.command('--portal-only'),env=self.env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        records=[]
        for process in (first,second):
            out,err=process.communicate(timeout=8);self.assertEqual(process.returncode,0,err);records.append(json.loads(out))
        self.assertTrue(self.mounted(self.runtime/'doc'))
        self.assertTrue((self.runtime/'doc/by-app').is_dir())
        self.assertEqual(sum(r['document_portal_recovered'] for r in records),1)
        self.assertEqual(len(self.calls.read_text().splitlines()),1)

    def test_disabled_recovery_fails_without_starting_any_portal(self):
        self.policy['recover_document_portal']=False;self.save()
        result=self.call('--portal-only');self.assertNotEqual(result.returncode,0)
        self.assertFalse(json.loads(result.stdout)['ok']);self.assertFalse(self.calls.exists())
        self.assertFalse(self.mounted(self.runtime/'doc'))

    def test_missing_sandbox_runtime_is_a_reported_failure(self):
        program=self.base/'flatpak-probe'
        program.write_text('#!/bin/sh\nprintf "runtime is not installed\\n" >&2\nexit 7\n');program.chmod(0o755)
        self.policy['flatpak_program']=str(program);self.save()
        result=self.call('--flatpak','org.mios.Probe');self.assertNotEqual(result.returncode,0)
        self.assertIn('not installed',json.loads(result.stdout)['error'])

    def test_stuck_sandbox_probe_times_out(self):
        warm=self.call('--portal-only');self.assertEqual(warm.returncode,0,warm.stderr)
        program=self.base/'flatpak-probe';pidfile=self.base/'probe.pid'
        program.write_text('#!/bin/sh\nprintf "%s\\n" "$$" > '+str(pidfile)+'\nsleep 30 & wait\n');program.chmod(0o755)
        self.policy.update(flatpak_program=str(program),overall_timeout_ms=1000,probe_timeout_ms=200);self.save()
        started=time.monotonic();result=self.call('--flatpak','org.mios.Probe')
        self.assertNotEqual(result.returncode,0);self.assertLess(time.monotonic()-started,2)
        self.assertIn('timed out',json.loads(result.stdout)['error'])
        self.assertFalse(Path('/proc/'+pidfile.read_text().strip()).exists(),'timed-out probe process leaked')

    def test_invalid_desktop_id_fails_before_recovery(self):
        result=self.call('--desktop','../escape');self.assertNotEqual(result.returncode,0)
        self.assertIn('Invalid application ID',result.stdout);self.assertFalse(self.calls.exists())

if __name__=='__main__':unittest.main()
