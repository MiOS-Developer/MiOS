#!/usr/bin/env python3
# AI-hint: Opt-in real FreeRDP MS-RDPEAI microphone forwarding to a native PipeWire source; synthetic tone positive, disabled permission negative and attached-server shutdown cleanup.
# AI-related: src/mios-rdp-server/crates/mios-rdp-server/src/redirect.rs, tests/fixtures/mios-rdp/e2e.py, usr/share/mios/mios.toml
# AI-functions: pa
import array
import importlib.util
import json
import math
import os
from pathlib import Path
import select
import signal
import subprocess
import tempfile
import threading
import time

import argparse

parser = argparse.ArgumentParser(description="Opt-in RDP microphone test; use a private test-auth server binary and an owned nested desktop descriptor")
parser.add_argument('--nested-descriptor', required=True)
parser.add_argument('--port', type=int, default=18475)
parser.add_argument('--report', required=True)
args = parser.parse_args()
uid = os.getuid()
assert uid != 0, 'run this fixture as the desktop operator'
assert 1024 <= args.port <= 65535
assert os.environ.get('MIOS_RDP_NATIVE_LIVE') == '1', 'explicit opt-in MIOS_RDP_NATIVE_LIVE=1 is required'
assert os.environ.get('MIOS_RDP_BIN'), 'point MIOS_RDP_BIN at a private test-auth build; never replace the production binary' 
os.environ.setdefault('ST_BRIDGE', '/usr/libexec/mios/mios-rdp-rfb-bridge')
os.environ['PULSE_SERVER'] = f'unix:/run/user/{uid}/pulse/native'
spec = importlib.util.spec_from_file_location('rdp_fixture', str(Path(__file__).with_name('e2e.py')))
e = importlib.util.module_from_spec(spec); spec.loader.exec_module(e)
desc = json.loads(Path(args.nested_descriptor).read_text())
e.RT = desc['runtime']
e.ENV = dict(os.environ, XDG_RUNTIME_DIR=e.RT, HYPRLAND_INSTANCE_SIGNATURE=desc['instance']['instance'], WAYLAND_DISPLAY=desc['instance']['wl_socket'])
e.ENV.pop('DISPLAY', None)

def pa(*args): return subprocess.check_output(['pactl', *args], text=True, timeout=6).strip()
before = (pa('get-default-source'), pa('get-default-sink'))
root = Path(tempfile.mkdtemp(prefix='mios-remote-mic-network-', dir='/var/tmp'))
synthetic = 'mios-remote-mic-network-' + str(os.getpid())
server_prefix = 'mios-remote-mic-network-server-' + str(os.getpid())
fifo = root / 'input.fifo'
module = None
results = []
try:
    module = pa('load-module', 'module-pipe-source', 'source_name='+synthetic, 'file='+str(fifo), 'format=s16le', 'rate=48000', 'channels=1')
    for enabled in (True, False):
        case = root / ('enabled' if enabled else 'disabled'); case.mkdir()
        e.ensure_cert(str(case))
        mon = e.mons()[0]
        e.write_config(str(case), port=args.port, mode='mirror', runtime_dir=e.RT, hypr_instance=desc['instance']['instance'],
            primary_output=mon['name'], operator_uid=uid, target_hz=30, min_hz=15, redirect={'microphone':enabled},
            naming={'microphone_source_prefix':server_prefix})
        s = e.Session(str(case), 'microphone', port=args.port)
        capture = None; writer = None; stopped = threading.Event(); writer_errors = []
        try:
            s.start_server(); s.start_xvfb('640x480')
            s.start_client(['/sec:tls', '/size:640x480', '-clipboard', '-grab-keyboard', '-grab-mouse', '/microphone:sys:pulse,dev:'+synthetic])
            assert s.wait_attached(), 'private FreeRDP client did not attach'
            deadline = time.monotonic() + (10 if enabled else 3)
            stats = {}
            while time.monotonic() < deadline:
                stats = s.stats()
                if stats.get('microphone',{}).get('native_ready'): break
                time.sleep(.1)
            if not enabled:
                assert not stats.get('microphone',{}).get('native_ready'), stats
                assert server_prefix not in pa('list','short','sources')
                results.append({'enabled':False,'native_source_created':False,'passed':True})
                continue
            mic = stats.get('microphone',{})
            assert mic.get('native_ready'), ('microphone did not open', mic, Path(s.out+'/server.log').read_text()[-3000:])
            source = mic['source']
            capture = subprocess.Popen(['parec','--device='+source,'--raw','--format=s16le','--rate=48000','--channels=1','--latency-msec=10'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            def feed():
                fd = None
                try:
                    fd = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOFOLLOW)
                    packet = 0
                    while not stopped.is_set():
                        pcm = array.array('h', [0 if packet < 20 else int(12000 * math.sin(2*math.pi*440*(packet*480+i)/48000)) for i in range(480)]).tobytes()
                        try: os.write(fd, pcm)
                        except BlockingIOError: pass
                        packet += 1; time.sleep(.01)
                except Exception as exc: writer_errors.append(repr(exc))
                finally:
                    if fd is not None: os.close(fd)
            writer = threading.Thread(target=feed, daemon=True); writer.start()
            data = bytearray(); deadline = time.monotonic()+8
            while len(data) < 480000 and time.monotonic() < deadline:
                ready,_,_ = select.select([capture.stdout],[],[],.2)
                if ready:
                    chunk = os.read(capture.stdout.fileno(),8192)
                    if not chunk: break
                    data += chunk
            stopped.set(); writer.join(1)
            assert not writer_errors, writer_errors
            pcm = array.array('h'); pcm.frombytes(data[:len(data)//2*2])
            rms = math.sqrt(sum(v*v for v in pcm)/max(1,len(pcm)))
            assert len(pcm) >= 9600 and rms > 1000, (len(pcm), rms)
            time.sleep(1)
            stats = s.stats(); assert stats['microphone']['bytes_forwarded'] >= 19200, stats['microphone']
            s.server.send_signal(signal.SIGTERM)
            assert s.server.wait(timeout=15) == 0, 'server must shut down cleanly while the client is attached'
            results.append({'enabled':True,'samples':len(pcm),'rms':round(rms,2),'microphone':stats['microphone'],'attached_server_signal_cleanup':True,'passed':True})
        finally:
            stopped.set()
            if writer: writer.join(1)
            if capture:
                capture.terminate()
                try: capture.wait(2)
                except subprocess.TimeoutExpired: capture.kill(); capture.wait()
            s.stop()
            deadline = time.monotonic()+6
            while server_prefix in pa('list','short','sources') and time.monotonic()<deadline: time.sleep(.1)
            assert server_prefix not in pa('list','short','sources'), 'session microphone leaked'
finally:
    if module: pa('unload-module', module)
    if fifo.exists(): fifo.unlink()
assert before == (pa('get-default-source'),pa('get-default-sink')), 'physical defaults changed'
report = {'passed':True,'results':results,'physical_microphone_recorded':False,'default_audio_unchanged':True,'native_sources_removed':True,'nested_left_running':True,'work_dir':str(root)}
Path(args.report).write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report), flush=True)
print('MIOS_MICROPHONE_NETWORK_DONE', flush=True)
