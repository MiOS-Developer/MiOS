#!/usr/bin/env python3
# AI-hint: Opt-in native RDPDR/FUSE integration with a real FreeRDP client: shared-file reads/writes/rename/truncate, invalid-name and disabled-permission controls, attached-server shutdown and owned mount cleanup.
# AI-related: src/mios-rdp-server/crates/mios-rdp-server/src/remote_storage.rs, tests/fixtures/mios-rdp/e2e.py, usr/share/mios/mios.toml
# AI-functions: mounts, cleanup

import argparse
import errno
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time

parser = argparse.ArgumentParser(description='Opt-in RDPDR native shared-folder integration; use an owned nested desktop and a private test-auth binary')
parser.add_argument('--nested-descriptor', required=True)
parser.add_argument('--report', required=True)
parser.add_argument('--port', type=int, default=18476)
parser.add_argument('--managed-service',action='store_true',help='exercise the production mixed-kill service lifecycle in an owned transient user unit')
args = parser.parse_args()
assert os.getuid() != 0, 'run this fixture as the desktop operator'
assert os.environ.get('MIOS_RDP_NATIVE_LIVE') == '1', 'explicit live-test opt-in is required'
assert os.environ.get('MIOS_RDP_BIN'), 'MIOS_RDP_BIN must select a private test-auth binary'
assert Path(os.environ['MIOS_RDP_BIN']).resolve() != Path('/usr/libexec/mios/rdp-native/mios-rdp-server').resolve()
assert 1024 <= args.port <= 65535
os.environ.setdefault('ST_BRIDGE', '/usr/libexec/mios/mios-rdp-rfb-bridge')
spec = importlib.util.spec_from_file_location('rdp_fixture', Path(__file__).with_name('e2e.py'))
e = importlib.util.module_from_spec(spec)
spec.loader.exec_module(e)
desc = json.loads(Path(args.nested_descriptor).read_text())
e.RT = desc['runtime']
e.ENV = dict(os.environ, XDG_RUNTIME_DIR=e.RT, HYPRLAND_INSTANCE_SIGNATURE=desc['instance']['instance'], WAYLAND_DISPLAY=desc['instance']['wl_socket'])
e.ENV.pop('DISPLAY', None)

class ManagedProcess:
    def __init__(self,unit,env):
        self.unit,self.env,self.stopping=unit,env,False
    def poll(self):
        result=subprocess.run(['systemctl','--user','show',self.unit,'-p','ActiveState','-p','ExecMainStatus','-p','Result'],env=self.env,capture_output=True,text=True,timeout=5)
        values=dict(line.split('=',1) for line in result.stdout.splitlines() if '=' in line)
        if values.get('ActiveState') in ('active','activating','deactivating'):return None
        code=int(values.get('ExecMainStatus','0'))
        return code if code or self.stopping else 1
    def send_signal(self,signum):
        self.stopping=True
        subprocess.run(['systemctl','--user','--no-block','stop',self.unit],env=self.env,check=True,timeout=5)
    def wait(self,timeout):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            code=self.poll()
            if code is not None:return code
            time.sleep(.1)
        raise subprocess.TimeoutExpired([self.unit],timeout)
    def kill(self):
        subprocess.run(['systemctl','--user','kill','--kill-whom=all','--signal=KILL',self.unit],env=self.env,timeout=5)

class ManagedSession(e.Session):
    def start_server(self):
        unit=f'mios-rdp-storage-test-{os.getpid()}-'+Path(self.d).name
        manager=dict(os.environ,XDG_RUNTIME_DIR=f'/run/user/{os.getuid()}',DBUS_SESSION_BUS_ADDRESS=f'unix:path=/run/user/{os.getuid()}/bus')
        env=dict(e.ENV,**self.server_env)
        command=['systemd-run','--user','--unit='+unit,'--property=KillMode=mixed','--property=TimeoutStopSec=15',
            '--property=StandardOutput=append:'+self.out+'/server.log','--property=StandardError=append:'+self.out+'/server.log']
        for key in ['XDG_RUNTIME_DIR','HYPRLAND_INSTANCE_SIGNATURE','WAYLAND_DISPLAY','PATH','MIOS_RDP_LOG']:
            if key in env:command.append('--setenv='+key+'='+env[key])
        command += [e.BIN,'--config',self.d+'/native.json']
        subprocess.run(command,env=manager,check=True,capture_output=True,text=True,timeout=10)
        self.server=ManagedProcess(unit,manager)
        for _ in range(100):
            if f'127.0.0.1:{self.port}' in subprocess.run(['ss','-ltn'],capture_output=True,text=True).stdout:return
            if self.server.poll() is not None:raise RuntimeError('managed test server exited before listening')
            time.sleep(.1)
        raise RuntimeError('managed test server did not listen')

def mounts():
    return {line.split()[4] for line in Path('/proc/self/mountinfo').read_text().splitlines() if ' - fuse.mios-rdp-drive ' in line}

before_mounts = mounts()
before_geometry = e.local_snapshot()
root = Path(tempfile.mkdtemp(prefix='mios-remote-files-network-', dir='/var/tmp'))
client = root / 'client-files'
client.mkdir()
data = bytes(range(256)) * 1024
(client / 'from-client.bin').write_bytes(data)
results = []
for enabled in (True, False):
    case = root / ('enabled' if enabled else 'disabled')
    case.mkdir()
    e.ensure_cert(str(case))
    mon = e.mons()[0]
    e.write_config(str(case), port=args.port, mode='mirror', runtime_dir=e.RT,
        operator_uid=os.getuid(), hypr_instance=desc['instance']['instance'], primary_output=mon['name'],
        target_hz=30, min_hz=15, redirect={'drives':enabled, 'microphone':False},
        native={'drive_timeout_ms':2000, 'drive_io_kib':16, 'drive_max_entries':128})
    s = (ManagedSession if args.managed_service else e.Session)(str(case), 'storage', port=args.port)
    owned = []
    try:
        s.start_server()
        s.start_xvfb('640x480')
        s.start_client(['/sec:tls','/size:640x480','-clipboard','-grab-keyboard','-grab-mouse',f'/drive:files,{client}'])
        assert s.wait_attached(), 'private client did not attach'
        deadline = time.monotonic() + (10 if enabled else 3)
        forwarded = {}
        while time.monotonic() < deadline:
            forwarded = s.stats().get('device_forwarding',{}).get('drives',{})
            if forwarded.get('mounts'): break
            time.sleep(.1)
        if not enabled:
            assert not forwarded.get('mounts'), forwarded
            assert mounts() == before_mounts, 'disabled channel created a native mount'
            results.append({'enabled':False,'native_mount_created':False,'passed':True})
            continue
        assert forwarded.get('mounts'), ('client drive not mounted',forwarded,Path(s.out+'/server.log').read_text()[-3000:])
        owned = [Path(item['path']) for item in forwarded['mounts']]
        assert len(owned) == 1, forwarded
        mount = owned[0]
        assert mount.is_relative_to(Path(e.RT)/'mios-rdp/drives'), 'mount outside owned runtime'
        assert str(mount) in mounts() and mount.is_mount(), 'empty directory is not a native filesystem'
        volume = os.statvfs(mount)
        assert volume.f_blocks > 0 and volume.f_frsize > 0, 'native apps need real remote volume capacity'
        assert (mount/'from-client.bin').read_bytes() == data
        assert 'from-client.bin' in os.listdir(mount)
        sent = b'native-server-to-client\x00' * 4096
        assert (mount/'from-server.bin').write_bytes(sent) == len(sent)
        assert (client/'from-server.bin').read_bytes() == sent
        (mount/'subfolder').mkdir()
        (mount/'subfolder'/'nested.txt').write_text('nested file')
        assert (client/'subfolder'/'nested.txt').read_text() == 'nested file'
        (mount/'from-server.bin').rename(mount/'renamed.bin')
        assert not (client/'from-server.bin').exists()
        assert (client/'renamed.bin').read_bytes() == sent
        with (mount/'renamed.bin').open('r+b') as handle:
            handle.truncate(17)
        assert (client/'renamed.bin').read_bytes() == sent[:17]
        try:
            (mount/'bad\\escape').write_text('must be rejected')
        except OSError as error:
            assert error.errno == errno.EINVAL, error
        else:
            raise AssertionError('a Windows path separator bypassed component validation')
        assert not (client/'bad\\escape').exists()
        (mount/'subfolder'/'nested.txt').unlink()
        (mount/'subfolder').rmdir()
        assert not (client/'subfolder').exists()
        # Unlinking an open file must not redirect its old handle to a new file
        # subsequently created under the same pathname.
        (mount/'reuse.txt').write_bytes(b'old-file')
        with (mount/'reuse.txt').open('rb') as held:
            (mount/'reuse.txt').unlink()
            (mount/'reuse.txt').write_bytes(b'new-file')
            assert held.read() == b'old-file'
        assert (client/'reuse.txt').read_bytes() == b'new-file'
        s.server.send_signal(signal.SIGTERM)
        assert s.server.wait(timeout=15) == 0, 'server failed attached-session shutdown'
        assert not any(str(path) in mounts() for path in owned), 'native filesystem leaked on shutdown'
        results.append({'enabled':True,'client_to_server_bytes':len(data),'server_to_client_bytes':len(sent),
            'sha256':hashlib.sha256(data).hexdigest(),'directory_rename_truncate_delete':True,
            'invalid_component_rejected':True,'attached_shutdown_unmounted':True,'passed':True})
    finally:
        s.stop()
        leaked = [path for path in owned if str(path) in mounts()]
        for path in leaked:
            assert path.is_relative_to(Path(e.RT)/'mios-rdp/drives')
            subprocess.run(['fusermount3','-u','-z',str(path)], check=True, timeout=5)
        assert not leaked, 'owned FUSE mount needed emergency cleanup; test is not a pass'
assert mounts() == before_mounts, 'native drive mount set changed'
assert e.local_snapshot() == before_geometry, 'nested display geometry changed'
report = {'passed':True,'results':results,'mounts_removed':True,'nested_geometry_unchanged':True,
    'nested_left_running':True,'managed_service':args.managed_service,'work_dir':str(root)}
Path(args.report).write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report),flush=True)
print('MIOS_STORAGE_NETWORK_DONE',flush=True)
