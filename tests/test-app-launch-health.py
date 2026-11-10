#!/usr/bin/env python3
# AI-hint: Two-sided compiled launcher checks with private document-portal buses/runtime dirs: mount recovery, concurrent coalescing, disabled recovery, sandbox errors, timeouts and invalid desktop IDs. Never restart the operator's portal.
# AI-related: tools/native/mios-gen/src/app_preflight.rs, tests/test-document-portal-isolation.py, usr/share/mios/quickshell/AppLaunch.qml
# AI-functions: LauncherHealth, BrokerEnvironmentTests, DesktopDispatchTests

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



class BrokerEnvironmentTests(unittest.TestCase):
    def setUp(self):
        import importlib.machinery,importlib.util,socket
        path=ROOT/"usr/libexec/mios/mios-launcher-daemon"
        loader=importlib.machinery.SourceFileLoader("mios_launcher_fixture",str(path))
        spec=importlib.util.spec_from_loader(loader.name,loader)
        self.module=importlib.util.module_from_spec(spec);loader.exec_module(self.module)
        self.temp=tempfile.TemporaryDirectory(prefix="mios-broker-context-")
        self.runtime=Path(self.temp.name)/"runtime";self.runtime.mkdir(mode=0o700)
        self.signature="current-fixture";self.folder=self.runtime/"hypr"/self.signature;self.folder.mkdir(parents=True)
        self.control=self.folder/".socket.sock";self.display=self.runtime/"wayland-test"
        self.control_server=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);self.control_server.bind(str(self.control));self.control_server.listen(8)
        self.display_server=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);self.display_server.bind(str(self.display))
        self.base={"HOME":"/operator","XDG_RUNTIME_DIR":str(self.runtime),"HYPRLAND_INSTANCE_SIGNATURE":"stale","WAYLAND_DISPLAY":"stale","BROKER_SETTING":"keep"}
        self.manager="XDG_CURRENT_DESKTOP=Hyprland\nHYPRLAND_INSTANCE_SIGNATURE=current-fixture\nWAYLAND_DISPLAY=wayland-test\nDISPLAY=:91\nPRIVATE_SESSION_VALUE=must-not-import\nLD_PRELOAD=must-not-import\nHOME=/must-not-import\n"

    def tearDown(self):
        self.control_server.close();self.display_server.close();self.temp.cleanup()

    def refresh(self,text=None,uid=None):
        return self.module._refresh_gui_environment(self.base,self.manager if text is None else text,self.runtime,os.getuid() if uid is None else uid,.2)

    def test_refresh_uses_only_gui_allowlist_and_live_owned_sockets(self):
        result=self.refresh()
        self.assertEqual(result["HOME"],"/operator")
        self.assertEqual(result["HYPRLAND_INSTANCE_SIGNATURE"],self.signature)
        self.assertEqual(result["WAYLAND_DISPLAY"],"wayland-test")
        self.assertEqual(result["BROKER_SETTING"],"keep")
        self.assertNotIn("PRIVATE_SESSION_VALUE",result);self.assertNotIn("LD_PRELOAD",result)
        self.assertEqual(self.base["HYPRLAND_INSTANCE_SIGNATURE"],"stale","refresh must not mutate shared broker environment")
        target=self.module._execution_context(result)
        self.assertEqual(target,{"id":str(self.control.resolve()),"platform":"linux","session_id":self.signature,"backend":"hyprland"})

    def test_removed_manager_values_do_not_retain_stale_compositor_identity(self):
        result=self.refresh("XDG_CURRENT_DESKTOP=GNOME\nDISPLAY=:91\n")
        self.assertNotIn("HYPRLAND_INSTANCE_SIGNATURE",result)
        self.assertNotIn("WAYLAND_DISPLAY",result)

    def test_missing_or_escaped_compositor_identity_is_rejected(self):
        for signature in ("../escape","-bad/name",""):
            text=self.manager.replace("HYPRLAND_INSTANCE_SIGNATURE=current-fixture","HYPRLAND_INSTANCE_SIGNATURE="+signature)
            with self.assertRaises(RuntimeError):self.refresh(text)

    def test_foreign_runtime_owner_is_rejected(self):
        with self.assertRaises(RuntimeError):self.refresh(uid=os.getuid()+1)

    def test_headless_capture_strips_stale_gui_but_graphical_dispatch_still_refuses(self):
        from unittest import mock
        from types import SimpleNamespace
        sys.path.insert(0,str(ROOT/"usr/lib/mios"))
        import mios_toml
        stale="XDG_CURRENT_DESKTOP=Hyprland\nWAYLAND_DISPLAY=dead-display\n"
        with mock.patch.dict(os.environ,{"XDG_RUNTIME_DIR":str(self.runtime)}), \
             mock.patch.object(mios_toml,"load_merged",return_value={"desktop":{"launch":{"probe_timeout_ms":200}}}), \
             mock.patch.object(self.module.subprocess,"run",return_value=SimpleNamespace(returncode=0,stdout=stale)):
            result=self.module._operator_environment(allow_headless=True)
            self.assertFalse(self.module._GUI_SESSION_KEYS.intersection(result))
            self.assertIsNone(self.module._execution_context(result))
            with self.assertRaises(RuntimeError):self.module._operator_environment()
            self.runtime.chmod(0o777)
            with self.assertRaises(RuntimeError):self.module._operator_environment(allow_headless=True)

    def test_foreign_socket_owner_is_rejected(self):
        from unittest import mock
        from types import SimpleNamespace
        original=Path.lstat
        control=self.control
        def metadata(path):
            found=original(path)
            return SimpleNamespace(st_mode=found.st_mode,st_uid=os.getuid()+1) if path==control else found
        with mock.patch.object(Path,"lstat",metadata):
            with self.assertRaises(RuntimeError):self.refresh()

    def test_dead_compositor_socket_and_display_symlink_are_rejected(self):
        import socket
        self.control_server.close()
        with self.assertRaises(OSError):self.refresh()
        self.control.unlink()
        self.control_server=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);self.control_server.bind(str(self.control));self.control_server.listen(2)
        self.display.unlink();self.display.symlink_to(self.control)
        with self.assertRaises(RuntimeError):self.refresh()

    def test_private_capture_reports_context_failure_without_executing_request(self):
        import socket,threading
        from unittest import mock
        server,client=socket.socketpair()
        with mock.patch.object(self.module,"_operator_environment",side_effect=RuntimeError("stale context")), mock.patch.object(self.module.subprocess,"run",side_effect=AssertionError("must not execute")):
            thread=threading.Thread(target=self.module.handle,args=(server,),daemon=True);thread.start()
            client.sendall(b"CAPTURE_JSON: printf forbidden\n")
            response=bytearray()
            while not response.endswith(b"\n"):
                chunk=client.recv(4096)
                if not chunk:break
                response.extend(chunk)
            thread.join(timeout=2);client.close()
        record=json.loads(response);self.assertEqual(record["exit_code"],1)
        self.assertIn("stale context",record["stderr"]);self.assertEqual(record["stdout"],"")


class DesktopDispatchTests(unittest.TestCase):
    """Exercise the actual launcher bridge with a private broker and fake GUI."""
    def setUp(self):
        import pwd
        self.temp=tempfile.TemporaryDirectory(prefix="mios-desktop-dispatch-")
        self.base=Path(self.temp.name);self.bin=self.base/"bin";self.bin.mkdir()
        self.private=self.base/"private-home";self.private.mkdir()
        self.calls=self.base/"launch.json";self.preflight=self.base/"preflight.json"
        self.user=pwd.getpwuid(os.getuid()).pw_name
        self.home=pwd.getpwuid(os.getuid()).pw_dir
        self.sock=self.base/"broker.sock";self.request=""
        source=(ROOT/"usr/libexec/mios/mios-launch").read_text()
        self.code=source.split("<<'MIOS_DESKTOP_DISPATCH'\n",1)[1].split("\nMIOS_DESKTOP_DISPATCH",1)[0]
        self.env=dict(os.environ,PATH=str(self.bin)+os.pathsep+os.defpath,
            HOME=str(self.private),MIOS_LAUNCHER_SOCK=str(self.sock))
        self.env.pop("DISPLAY",None);self.env.pop("WAYLAND_DISPLAY",None)
        self.env["MIOS_APP_TEST_CALLS"]=str(self.calls)
        self.env["MIOS_APP_TEST_PREFLIGHT"]=str(self.preflight)
        self.program("systemctl","""import os,sys
if os.environ.get('MIOS_APP_TEST_MANAGER_FAIL'): raise SystemExit(7)
print('WAYLAND_DISPLAY=wayland-test')
print('DISPLAY=:91')
print('HYPRLAND_INSTANCE_SIGNATURE=current-fixture')
print('HOME=/must-not-import')
print('LD_PRELOAD=/must-not-import')
""")
        self.program("mios-gen","""import json,os,sys
from pathlib import Path
if '--sessions' in sys.argv:
    if os.environ.get('MIOS_APP_TEST_INVENTORY_FAIL'): raise SystemExit(8)
    print(json.dumps({'ok':True,'sessions':[{'execution_target':json.loads(os.environ['MIOS_APP_TEST_TARGET']),'runtime':os.environ['MIOS_APP_TEST_RUNTIME'],'available':True,'scope':'operator'}],'registered_endpoints':[]}))
else:
    Path(os.environ['MIOS_APP_TEST_PREFLIGHT']).write_text(json.dumps(sys.argv[1:]))
    if os.environ.get('MIOS_APP_TEST_PREFLIGHT_FAIL'): raise SystemExit(7)
    print(json.dumps({'ok':True}))
""")
        self.launcher=self.program("desktop-launch","""import json,os,sys
from pathlib import Path
with Path(os.environ['MIOS_APP_TEST_CALLS']+".count").open("a") as receipt:receipt.write("1\\n")
Path(os.environ['MIOS_APP_TEST_CALLS']).write_text(json.dumps({'argv':sys.argv[1:],'home':os.environ['HOME'],'xdg_config':os.environ['XDG_CONFIG_HOME'],'wayland':os.environ.get('WAYLAND_DISPLAY'),'signature':os.environ.get('HYPRLAND_INSTANCE_SIGNATURE'),'preload':os.environ.get('LD_PRELOAD')}))
receipt={'dispatched':True,'confirmed':False,'method':'exec','pid':os.getpid(),'desktop_id':'org.example.Recorder','desktop_file':sys.argv[1],'execution_target':json.loads(os.environ['MIOS_DESKTOP_DISPATCH_TARGET'])}
if os.environ.get('MIOS_APP_TEST_WRONG_TARGET'):receipt['execution_target']={'id':'unrelated'}
if os.environ.get('MIOS_APP_TEST_WRONG_APP'):receipt['desktop_id']='org.example.Other'
if os.environ.get('MIOS_APP_TEST_NO_EXEC'):receipt.pop('pid')
print(json.dumps(receipt))

""")
        import socket
        self.runtime=self.base/"runtime";self.runtime.mkdir(mode=0o700)
        control=self.runtime/"hypr/current-fixture/.socket.sock";control.parent.mkdir(parents=True)
        self.sockets=[socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) for _ in range(2)]
        self.sockets[0].bind(str(control));self.sockets[1].bind(str(self.runtime/"wayland-test"))
        self.destination={"id":str(control),"platform":"linux","session_id":"current-fixture","backend":"hyprland"}
        self.env["MIOS_EXECUTION_SESSION"]=str(control)
        self.env["MIOS_APP_TEST_RUNTIME"]=str(self.runtime)
        self.env["MIOS_APP_TEST_TARGET"]=json.dumps(self.destination)
        helper=self.bin/"mios-computer-use"
        helper.write_text(chr(10).join(["import runpy","globals().update(runpy.run_path("+repr(str(ROOT/"usr/libexec/mios/mios-computer-use"))+"))",""]))
        self.program("hyprctl","import json; print(json.dumps([{'instance':'current-fixture','wl_socket':'wayland-test'}]))")
        self.record={"ok":True,"desktop_id":"org.example.Recorder","desktop_file":str(self.base/"record.desktop"),"operator_user":self.user,"overall_timeout_ms":3000}

    def program(self,name,code):
        import sys
        p=self.bin/name;p.write_text("#!"+sys.executable+"\n"+code);p.chmod(0o755);return p

    def broker(self):
        import socket,threading
        server=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);server.bind(str(self.sock));server.listen(1)
        def serve():
            with server,server.accept()[0] as connection:
                data=bytearray()
                while not data.endswith(b"\n"):
                    part=connection.recv(4096)
                    if not part:break
                    data.extend(part)
                self.request=data.decode()
                if self.request.count("\n")!=1:
                    result={"stdout":"","stderr":"invalid request framing","exit_code":2}
                else:
                    command=self.request.removeprefix("CAPTURE_JSON: ").rstrip("\n")
                    done=subprocess.run(["bash","--noprofile","--norc","-c",command],env=self.env,capture_output=True,text=True,timeout=5)
                    result={"stdout":done.stdout,"stderr":done.stderr,"exit_code":done.returncode}
                    if getattr(self,"corrupt_receipt",False):result["stdout"]="malformed"
                connection.sendall((json.dumps(result)+"\n").encode())
        self.thread=threading.Thread(target=serve,daemon=True);self.thread.start()

    def invoke(self,*args,record=None):
        import sys
        return subprocess.run([sys.executable,"-c",self.code,json.dumps(record or self.record),str(self.launcher),*args],env=self.env,capture_output=True,text=True,timeout=8)

    def tearDown(self):
        if hasattr(self,"thread"):self.thread.join(timeout=6)
        for connection in getattr(self,"sockets",[]):connection.close()
        self.temp.cleanup()

    def test_operator_broker_restores_home_and_current_manager_environment(self):
        self.broker();result=self.invoke("a file")
        self.assertEqual(result.returncode,0,result.stderr)
        receipt=json.loads(result.stdout);self.assertTrue(receipt["dispatched"]);self.assertFalse(receipt["confirmed"])
        self.assertEqual(receipt["execution_target"],self.destination)
        call=json.loads(self.calls.read_text())
        self.assertEqual(call["home"],self.home)
        self.assertEqual(call["xdg_config"],self.home+"/.config")
        self.assertEqual(call["wayland"],"wayland-test")
        self.assertEqual(call["signature"],"current-fixture")
        self.assertNotEqual(call["preload"],"/must-not-import")
        self.assertEqual(json.loads(self.preflight.read_text()),["app-preflight","--desktop","org.example.Recorder"])

    def test_shell_metacharacters_and_newlines_remain_literal_argv(self):
        proof=self.base/"must-not-execute"
        literal="a'; $(touch "+str(proof)+")\nsecond line"
        self.record["desktop_file"]=str(self.base/"a ' quoted.desktop")
        self.broker();result=self.invoke(literal)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertFalse(proof.exists())
        self.assertEqual(json.loads(self.calls.read_text())["argv"],[self.record["desktop_file"],literal])
        self.assertEqual(self.request.count("\n"),1)

    def test_unknown_broker_outcome_never_repeats_an_already_dispatched_launch(self):
        self.corrupt_receipt=True;self.broker()
        result=self.invoke();self.assertNotEqual(result.returncode,0)
        self.assertIn("outcome unavailable",result.stderr)
        self.assertTrue(self.calls.exists())
        self.assertEqual(len(Path(str(self.calls)+".count").read_text().splitlines()),1)

    def test_mismatched_or_missing_exec_receipts_fail_without_a_second_launch(self):
        for flag in ("MIOS_APP_TEST_WRONG_TARGET","MIOS_APP_TEST_WRONG_APP","MIOS_APP_TEST_NO_EXEC"):
            with self.subTest(flag=flag):
                self.env[flag]="1"
                self.broker();result=self.invoke();self.thread.join(timeout=6)
                self.assertNotEqual(result.returncode,0)
                self.assertIn("receipt does not match",result.stderr)
                self.assertEqual(len(Path(str(self.calls)+".count").read_text().splitlines()),1)
                self.sock.unlink();self.calls.unlink();Path(str(self.calls)+".count").unlink()
                self.env.pop(flag)

    def test_preflight_failure_does_not_dispatch_or_fall_back(self):
        self.env["MIOS_APP_TEST_PREFLIGHT_FAIL"]="1";self.broker()
        result=self.invoke();self.assertNotEqual(result.returncode,0)
        self.assertTrue(self.preflight.exists());self.assertFalse(self.calls.exists())

    def test_fresh_inventory_failure_does_not_dispatch_or_fall_back(self):
        self.env["MIOS_APP_TEST_INVENTORY_FAIL"]="1";self.broker()
        result=self.invoke();self.assertNotEqual(result.returncode,0)
        self.assertFalse(self.preflight.exists());self.assertFalse(self.calls.exists())

    def test_unavailable_manager_does_not_dispatch(self):
        self.env["MIOS_APP_TEST_MANAGER_FAIL"]="1";self.broker()
        result=self.invoke();self.assertNotEqual(result.returncode,0)
        self.assertFalse(self.preflight.exists());self.assertFalse(self.calls.exists())

    def test_missing_and_ambiguous_registry_entries_are_not_dispatched(self):
        missing=self.invoke(record={"ok":False,"status":"not_found","error":"not installed"})
        self.assertEqual(missing.returncode,64)
        ambiguous=self.invoke(record={"ok":False,"status":"ambiguous","error":"Ambiguous application"})
        self.assertEqual(ambiguous.returncode,2)
        self.assertFalse(self.calls.exists())

    def test_missing_broker_never_launches_as_another_identity(self):
        import pwd
        other=next((p for p in pwd.getpwall() if p.pw_uid!=os.getuid()),None)
        self.assertIsNotNone(other)
        self.record["operator_user"]=other.pw_name
        result=self.invoke();self.assertNotEqual(result.returncode,0)
        self.assertIn("refusing privileged GUI fallback",result.stderr)
        self.assertFalse(self.calls.exists())




class NativeDesktopDispatchTests(unittest.TestCase):
    """Real executable-start and broker receipts; all sockets/apps are private."""
    def setUp(self):
        import socket,threading,sys
        self.temp=tempfile.TemporaryDirectory(prefix="mios-native-dispatch-")
        self.base=Path(self.temp.name)
        self.runtime=self.base/"runtime";self.runtime.mkdir(mode=0o700)
        self.home=self.base/"home";self.home.mkdir()
        self.root=self.base/"root";(self.root/"usr/share/mios").mkdir(parents=True)
        self.apps=self.base/"applications";self.apps.mkdir()
        self.bin=self.base/"bin";self.bin.mkdir()
        self.calls=self.base/"app.json";self.scope_calls=self.base/"scope.json"
        self.pid=None
        self.env={k:v for k,v in os.environ.items() if not k.startswith("MIOS_") and k not in
                  ("DISPLAY","WAYLAND_DISPLAY","HYPRLAND_INSTANCE_SIGNATURE","DBUS_SESSION_BUS_ADDRESS")}
        self.env.update(HOME=str(self.home),XDG_CONFIG_HOME=str(self.home/"config"),
            XDG_DATA_HOME=str(self.home/"data"),XDG_DATA_DIRS=str(self.base/"empty"),
            XDG_RUNTIME_DIR=str(self.runtime),WAYLAND_DISPLAY="wayland-private",
            HYPRLAND_INSTANCE_SIGNATURE="receipt-fixture",MIOS_SECRET_FIXTURE="must-not-reach-app",
            PATH=str(self.bin)+os.pathsep+os.defpath)
        self.control=self.runtime/"hypr/receipt-fixture/.socket.sock"
        self.control.parent.mkdir(parents=True)
        self.server=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        self.server.bind(str(self.control));self.server.listen(8);self.server.settimeout(.1)
        self.wayland=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        self.wayland.bind(str(self.runtime/"wayland-private"))
        self.stop=threading.Event()
        def serve():
            while not self.stop.is_set():
                try:connection,_=self.server.accept()
                except TimeoutError:continue
                except OSError:break
                with connection:
                    connection.recv(1024)
                    connection.sendall(b'{"version":"private-receipt-fixture"}')
        self.thread=threading.Thread(target=serve,daemon=True);self.thread.start()
        self.target={"id":str(self.control),"platform":"linux","session_id":"receipt-fixture","backend":"hyprland"}
        self.env["MIOS_DESKTOP_DISPATCH_TARGET"]=json.dumps(self.target)
        self.app=self.program("private-app","""import json,os,sys,time
from pathlib import Path
Path(%r).write_text(json.dumps({'pid':os.getpid(),'argv':sys.argv[1:],
 'home':os.environ['HOME'],'wayland':os.environ.get('WAYLAND_DISPLAY'),
 'mios_keys':[key for key in os.environ if key.startswith('MIOS_')]}))
time.sleep(30)
""" % str(self.calls))
        self.scope=self.program("private-scope","""import json,os,subprocess,sys,time
from pathlib import Path
args=sys.argv[1:];Path(%r).write_text(json.dumps(args))
command=args[args.index('--')+1:]
mode=Path(%r).read_text() if Path(%r).exists() else 'good'
if mode=='failed':raise SystemExit(7)
if mode=='corrupt':
 result=subprocess.run(command,capture_output=True,text=True);print('malformed');raise SystemExit(result.returncode)
if mode=='timeout':
 subprocess.run(command,check=True);time.sleep(5);raise SystemExit(0)
os.execv(command[0],command)
""" % (str(self.scope_calls),str(self.base/"mode"),str(self.base/"mode")))
        self.manager=self.program("private-manager","import sys; raise SystemExit(0)")
        self.activation=self.program("private-activation","import sys; raise SystemExit(0)")
        self.desktop=self.apps/"org.example.Receipt.desktop"
        self.desktop.write_text("[Desktop Entry]\nType=Application\nName=Receipt\nExec="+str(self.app)+' "literal $HOME" %U\n')
        self.policy=tomllib.loads((ROOT/"usr/share/mios/mios.toml").read_text())["desktop"]["launch"].copy()
        self.policy.update(application_dirs=[str(self.apps)],overall_timeout_ms=1000,probe_timeout_ms=500,
            scope_program=str(self.scope),systemctl_program=str(self.manager),activation_program=str(self.activation))
        self.save()

    def program(self,name,code):
        import sys
        path=self.bin/name;path.write_text("#!"+sys.executable+"\n"+code);path.chmod(0o755);return path

    def save(self):
        (self.root/"usr/share/mios/mios.toml").write_text("[desktop.launch]\n"+
            "".join(key+" = "+json.dumps(value)+"\n" for key,value in self.policy.items()))

    def invoke(self,*args):
        done=subprocess.run([BINARY,"desktop-dispatch","--root",str(self.root),"--",str(self.desktop),*args],
            env=self.env,capture_output=True,text=True,timeout=5)
        for _ in range(100):
            if self.calls.exists():
                self.pid=json.loads(self.calls.read_text())["pid"];break
            if done.returncode:break
            time.sleep(.005)
        return done

    def tearDown(self):
        if self.pid:
            try:os.kill(self.pid,signal.SIGTERM)
            except ProcessLookupError:pass
        self.stop.set();self.server.close();self.wayland.close();self.thread.join(timeout=1)
        self.temp.cleanup()

    def test_long_lived_actual_exec_receipt_arrives_before_exit_with_exact_target(self):
        start=time.monotonic();result=self.invoke("file with spaces","$(touch should-not-execute)")
        self.assertEqual(result.returncode,0,result.stderr);self.assertLess(time.monotonic()-start,1)
        receipt=json.loads(result.stdout)
        self.assertEqual(receipt["pid"],self.pid);self.assertEqual(receipt["execution_target"],self.target)
        self.assertEqual(receipt["desktop_file"],str(self.desktop));self.assertFalse(receipt["confirmed"])
        self.assertTrue(Path("/proc",str(self.pid)).exists())
        call=json.loads(self.calls.read_text())
        self.assertEqual(call["argv"],["literal $HOME","file with spaces","$(touch should-not-execute)"])
        self.assertEqual(call["mios_keys"],[]);self.assertEqual(call["wayland"],"wayland-private")
        scope=json.loads(self.scope_calls.read_text())
        self.assertIn("--scope",scope);self.assertIn("--expand-environment=no",scope)
        self.assertNotIn("must-not-reach-app",json.dumps(scope))

    def test_actual_broker_capture_returns_native_receipt_while_app_remains_alive(self):
        import importlib.machinery,importlib.util,socket,threading,shlex
        from unittest import mock
        loader=importlib.machinery.SourceFileLoader("mios_native_broker",str(ROOT/"usr/libexec/mios/mios-launcher-daemon"))
        spec=importlib.util.spec_from_loader(loader.name,loader)
        module=importlib.util.module_from_spec(spec);loader.exec_module(module)
        # This private shell avoids loading an installed host login profile. The
        # real broker framing, capture subprocess and native scope child all run.
        self.program("bash","import os,sys; os.execv('/bin/bash',['/bin/bash','--noprofile','--norc','-c',sys.argv[-1]])")
        server,client=socket.socketpair();client.settimeout(4)
        command=shlex.join([BINARY,"desktop-dispatch","--root",str(self.root),"--",str(self.desktop),"from broker"])
        with mock.patch.object(module,"_operator_environment",return_value=dict(self.env)):
            thread=threading.Thread(target=module.handle,args=(server,),daemon=True);thread.start()
            client.sendall(("CAPTURE_JSON: "+command+"\n").encode())
            response=bytearray()
            while not response.endswith(b"\n"):
                response.extend(client.recv(4096))
            thread.join(timeout=2)
        client.close();server.close()
        envelope=json.loads(response)
        self.assertEqual(envelope["exit_code"],0,envelope["stderr"])
        receipt=json.loads(envelope["stdout"])
        self.pid=receipt["pid"]
        self.assertEqual(receipt["execution_target"],self.target)
        self.assertEqual(envelope["operator_context"],self.target)
        self.assertTrue(Path("/proc",str(self.pid)).exists())
        self.assertFalse(thread.is_alive())

    def test_failed_actual_exec_is_not_reported_as_success(self):
        self.desktop.write_text("[Desktop Entry]\nType=Application\nName=Missing\nExec=/mios-no-such-fixture-executable\n")
        result=self.invoke();self.assertNotEqual(result.returncode,0)
        self.assertIn("Cannot execute desktop application",result.stderr);self.assertFalse(self.calls.exists())

    def test_changed_target_is_rejected_before_any_dispatch(self):
        self.env["MIOS_DESKTOP_DISPATCH_TARGET"]=json.dumps(dict(self.target,id="/unrelated"))
        result=self.invoke();self.assertNotEqual(result.returncode,0)
        self.assertIn("target changed",result.stderr);self.assertFalse(self.scope_calls.exists());self.assertFalse(self.calls.exists())

    def test_unowned_or_symlinked_compositor_cannot_receive_a_dispatch(self):
        self.control.unlink();self.control.symlink_to(self.runtime/"wayland-private")
        result=self.invoke();self.assertNotEqual(result.returncode,0)
        self.assertFalse(self.calls.exists());self.assertFalse(self.scope_calls.exists())

    def test_unregistered_desktop_file_fails_without_dispatch(self):
        outside=self.base/"outside.desktop";outside.write_text(self.desktop.read_text())
        self.desktop=outside
        result=self.invoke();self.assertNotEqual(result.returncode,0)
        self.assertIn("installed registry",result.stderr);self.assertFalse(self.calls.exists())

    def test_ambiguous_canonical_desktop_path_fails_without_dispatch(self):
        (self.apps/"org.example.Other.desktop").symlink_to(self.desktop)
        result=self.invoke();self.assertNotEqual(result.returncode,0)
        self.assertIn("Ambiguous installed desktop",result.stderr);self.assertFalse(self.calls.exists())

    def test_corrupted_scope_receipt_does_not_repeat_a_started_app(self):
        (self.base/"mode").write_text("corrupt")
        result=self.invoke();self.assertNotEqual(result.returncode,0)
        for _ in range(100):
            if self.calls.exists():self.pid=json.loads(self.calls.read_text())["pid"];break
            time.sleep(.005)
        self.assertIsNotNone(self.pid);self.assertIn("valid exec receipt",result.stderr)
        self.assertTrue(Path("/proc",str(self.pid)).exists())

    def test_scope_timeout_reports_unknown_outcome_without_relaunch(self):
        (self.base/"mode").write_text("timeout")
        start=time.monotonic();result=self.invoke();self.assertNotEqual(result.returncode,0)
        self.assertLess(time.monotonic()-start,2);self.assertIn("refusing duplicate launch",result.stderr)
        self.assertIsNotNone(self.pid);self.assertTrue(Path("/proc",str(self.pid)).exists())

    def test_manager_failure_does_not_fall_back_after_scope_dispatch(self):
        (self.base/"mode").write_text("failed")
        result=self.invoke();self.assertNotEqual(result.returncode,0)
        self.assertIn("scope failed",result.stderr);self.assertFalse(self.calls.exists())

    def test_multiple_single_file_resources_preserve_gio_activation_contract(self):
        self.desktop.write_text("[Desktop Entry]\nType=Application\nName=Files\nExec=/mios-fixture-handler %f\n")
        result=self.invoke("first file","second file")
        self.assertEqual(result.returncode,0,result.stderr)
        receipt=json.loads(result.stdout);self.assertEqual(receipt["method"],"activation")
        self.assertIsNone(receipt["pid"]);self.assertFalse(self.calls.exists())

    def test_dbus_activation_retains_the_configured_gtk_fallback(self):
        output=self.base/"activation.json"
        fallback=self.program("private-fallback","import json,sys;from pathlib import Path;Path("+repr(str(output))+").write_text(json.dumps(sys.argv[1:]))")
        self.policy["activation_program"]="/mios-fixture-no-gio"
        self.policy["activation_fallback_program"]=str(fallback);self.save()
        self.desktop.write_text("[Desktop Entry]\nType=Application\nName=Activation\nDBusActivatable=true\n")
        result=self.invoke("literal resource")
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(output.read_text()),["org.example.Receipt","literal resource"])
        self.assertEqual(json.loads(result.stdout)["method"],"activation")

    def test_dbus_only_entry_reports_activation_without_a_fake_pid(self):
        self.desktop.write_text("[Desktop Entry]\nType=Application\nName=Activation\nDBusActivatable=true\n")
        result=self.invoke();self.assertEqual(result.returncode,0,result.stderr)
        receipt=json.loads(result.stdout);self.assertEqual(receipt["method"],"activation")
        self.assertIsNone(receipt["pid"]);self.assertFalse(self.calls.exists())


if __name__=='__main__':unittest.main()
