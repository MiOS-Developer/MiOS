#!/usr/bin/env python3
# AI-hint: Opt-in FreeRDP RDPSND speaker forwarding and PipeWire-Pulse restart recovery against two private, hardware-free PipeWire labs (server side A, client side B): tone into the session sink reaches FreeRDP (positive), disabled permission creates nothing (negative), the session microphone and speaker survive a restart of lab A's pipewire-pulse (positive) and do not without recovery (negative).
# AI-related: src/mios-rdp-server/crates/mios-rdp-server/src/remote_audio.rs, src/mios-rdp-server/crates/mios-rdp-server/src/redirect.rs, tests/fixtures/mios-rdp/e2e.py, tests/fixtures/mios-rdp/microphone.py, usr/share/mios/mios.toml, usr/share/doc/mios/remote-audio.md
# AI-functions: Lab, tone, rms, Capture, run_case
"""Run as the desktop operator with MIOS_RDP_NATIVE_LIVE=1, MIOS_RDP_BIN=<private test-auth build>,
--nested-descriptor <owned nested session.json> --report <owned report.json>. Every audio node lives in
a private PipeWire stack (own XDG_RUNTIME_DIR/HOME/state, no session bus, no hardware monitors); the
operator's PipeWire-Pulse is only read (defaults before/after) and never gets a node."""
import argparse
import array
import importlib.util
import json
import math
import os
from pathlib import Path
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument('--nested-descriptor', required=True)
ap.add_argument('--port', type=int, default=18476)
ap.add_argument('--report', required=True)
ap.add_argument('--cases', default='speaker,speaker-disabled,restart,mic-restart,restart-norecovery,default-priority')
ap.add_argument('--keep-work', action='store_true')
ap.add_argument('--pulse-namespace', action='store_true',
                help='pre-fix control binaries ignore audio_server: run the server in a private user+mount namespace whose '
                     '/run/user/<uid>/pulse is lab A (the host path is never changed)')
args = ap.parse_args()
uid = os.getuid()
assert uid != 0, 'run this fixture as the desktop operator'
assert 1024 <= args.port <= 65535
assert os.environ.get('MIOS_RDP_NATIVE_LIVE') == '1', 'explicit opt-in MIOS_RDP_NATIVE_LIVE=1 is required'
assert os.environ.get('MIOS_RDP_BIN'), 'point MIOS_RDP_BIN at a private test-auth build; never replace the production binary'
os.environ.setdefault('ST_BRIDGE', '/usr/libexec/mios/mios-rdp-rfb-bridge')
OPERATOR_PULSE = f'unix:/run/user/{uid}/pulse/native'
spec = importlib.util.spec_from_file_location('rdp_fixture', str(Path(__file__).with_name('e2e.py')))
e = importlib.util.module_from_spec(spec); spec.loader.exec_module(e)
desc = json.loads(Path(args.nested_descriptor).read_text())
inst = desc['instance']
e.RT = desc['runtime']
e.ENV = dict(os.environ, XDG_RUNTIME_DIR=e.RT, HYPRLAND_INSTANCE_SIGNATURE=inst['instance'], WAYLAND_DISPLAY=inst['wl_socket'])
e.ENV.pop('DISPLAY', None)
e.ENV.pop('PULSE_SERVER', None)


def operator_pa(*a):
    return subprocess.check_output(['pactl', *a], text=True, timeout=6, env=dict(os.environ, PULSE_SERVER=OPERATOR_PULSE)).strip()


class Lab:
    """Private PipeWire + WirePlumber (policy only, stateless, no hardware, no D-Bus) + PipeWire-Pulse."""

    def __init__(self, root, name):
        self.dir = Path(root) / name
        self.run = self.dir / 'run'
        self.home = self.dir / 'home'
        self.procs = {}
        for d in (self.run, self.home / '.config/pipewire/pipewire.conf.d', self.home / '.config/pipewire/pipewire-pulse.conf.d',
                  self.home / '.config/wireplumber/wireplumber.conf.d', self.dir / 'log'):
            d.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.run, 0o700)
        props = 'context.properties = { module.rt = false module.portal = false module.x11.bell = false module.jackdbus-detect = false }\n'
        (self.home / '.config/pipewire/pipewire.conf.d/10-private.conf').write_text(props)
        (self.home / '.config/pipewire/pipewire-pulse.conf.d/10-private.conf').write_text(props)
        (self.home / '.config/wireplumber/wireplumber.conf.d/10-private.conf').write_text(
            'wireplumber.profiles = { mios-private = { inherits = [ policy, mixin.systemwide-session, mixin.stateless ]\n'
            '  support.dbus = disabled  support.system-dbus = disabled  hardware.audio = disabled\n'
            '  hardware.bluetooth = disabled  hardware.video-capture = disabled } }\n')
        self.server = f'unix:{self.run}/pulse/native'
        self.env = {'PATH': '/usr/bin:/bin', 'HOME': str(self.home), 'XDG_RUNTIME_DIR': str(self.run),
                    'XDG_CONFIG_HOME': str(self.home / '.config'), 'XDG_STATE_HOME': str(self.home / '.local/state'),
                    'XDG_DATA_HOME': str(self.home / '.local/share'), 'PIPEWIRE_RUNTIME_DIR': str(self.run),
                    'DBUS_SESSION_BUS_ADDRESS': f'unix:path={self.dir}/no-bus', 'PULSE_SERVER': self.server}

    def _spawn(self, name, argv):
        log = open(self.dir / 'log' / f'{name}.log', 'a')
        self.procs[name] = subprocess.Popen(argv, env=self.env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)

    def _wait(self, path, timeout=8):
        deadline = time.monotonic() + timeout
        while not Path(path).exists():
            assert time.monotonic() < deadline, f'{path} never appeared'
            time.sleep(0.05)

    def start(self):
        self._spawn('pipewire', ['pipewire'])
        self._wait(self.run / 'pipewire-0')
        self._spawn('wireplumber', ['wireplumber', '-p', 'mios-private'])
        self.start_pulse()
        return self

    def start_pulse(self):
        sock = self.run / 'pulse' / 'native'
        if sock.exists():
            sock.unlink()
        self._spawn('pulse', ['pipewire-pulse'])
        self._wait(sock)
        deadline = time.monotonic() + 8
        while subprocess.run(['pactl', 'info'], env=self.env, capture_output=True, timeout=6).returncode != 0:
            assert time.monotonic() < deadline, 'private pipewire-pulse did not answer'
            time.sleep(0.1)
        # Stand-ins for the operator's hardware: explicit defaults with a device-like priority, so a
        # session node that tried to take over the default (or the AEC smart filter) would be visible.
        fifo = self.dir / 'default-mic.fifo'
        if fifo.exists():
            fifo.unlink()
        self.pa('load-module', 'module-null-sink', 'sink_name=lab-default-speaker',
                'sink_properties="device.description=Lab-Speaker priority.session=1500"')
        self.pa('load-module', 'module-pipe-source', 'source_name=lab-default-mic', f'file={fifo}', 'format=s16le', 'rate=48000',
                'channels=1', 'source_properties="device.description=Lab-Microphone priority.session=1500"')
        self.pa('set-default-sink', 'lab-default-speaker')
        self.pa('set-default-source', 'lab-default-mic')
        wait_for(lambda: self.defaults() == ('lab-default-speaker', 'lab-default-mic'), 5, 'lab defaults not applied')

    def restart_pulse(self):
        p = self.procs.pop('pulse')
        p.send_signal(signal.SIGTERM)
        p.wait(8)
        time.sleep(0.3)
        self.start_pulse()

    def pa(self, *a):
        return subprocess.check_output(['pactl', *a], text=True, timeout=6, env=self.env).strip()

    def defaults(self):
        return (self.pa('get-default-sink'), self.pa('get-default-source'))

    def stop(self):
        for name in ('pulse', 'wireplumber', 'pipewire'):
            p = self.procs.pop(name, None)
            if p and p.poll() is None:
                p.send_signal(signal.SIGTERM)
                try:
                    p.wait(5)
                except subprocess.TimeoutExpired:
                    p.kill(); p.wait()


def tone(seconds, rate=48000, channels=2, freq=440.0, amp=12000):
    frames = int(seconds * rate)
    return array.array('h', [int(amp * math.sin(2 * math.pi * freq * i / rate)) for i in range(frames) for _ in range(channels)]).tobytes()


def rms(data):
    pcm = array.array('h'); pcm.frombytes(data[:len(data) // 2 * 2])
    return math.sqrt(sum(v * v for v in pcm) / max(1, len(pcm))), len(pcm)


class Capture:
    """parec of one lab device for a bounded time; returns raw bytes."""

    def __init__(self, lab, device, channels=2):
        self.p = subprocess.Popen(['parec', f'--device={device}', '--raw', '--format=s16le', '--rate=48000', f'--channels={channels}',
                                   '--latency-msec=20'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=lab.env)

    def read(self, seconds, limit=48000 * 4 * 10):
        data = bytearray(); deadline = time.monotonic() + seconds
        while len(data) < limit and time.monotonic() < deadline:
            ready, _, _ = select.select([self.p.stdout], [], [], 0.1)
            if ready:
                chunk = os.read(self.p.stdout.fileno(), 65536)
                if not chunk:
                    break
                data += chunk
        return bytes(data)

    def close(self):
        if self.p.poll() is None:
            self.p.terminate()
            try:
                self.p.wait(2)
            except subprocess.TimeoutExpired:
                self.p.kill(); self.p.wait()


def play(lab, device, pcm, channels=2):
    p = subprocess.Popen(['pacat', f'--device={device}', '--raw', '--format=s16le', '--rate=48000', f'--channels={channels}'],
                         stdin=subprocess.PIPE, stderr=subprocess.DEVNULL, env=lab.env)
    p.stdin.write(pcm); p.stdin.close()
    return p


def wait_for(fn, timeout, what):
    deadline = time.monotonic() + timeout; last = None
    while time.monotonic() < deadline:
        last = fn()
        if last:
            return last
        time.sleep(0.1)
    raise AssertionError(f'{what} (last {last!r})')


root = Path(tempfile.mkdtemp(prefix='mios-remote-speaker-', dir='/var/tmp'))
os.chmod(root, 0o700)
pid = os.getpid()
operator_before = (operator_pa('get-default-sink'), operator_pa('get-default-source'))
lab_a = Lab(root, 'a').start()   # server-side audio server (session sink / microphone source)
lab_b = Lab(root, 'b').start()   # client-side audio server (FreeRDP playback sink / synthetic microphone)
defaults_a, defaults_b = lab_a.defaults(), lab_b.defaults()
client_sink = f'mios-rdp-client-speaker-{pid}'
synth = f'mios-rdp-client-mic-{pid}'
synth_fifo = lab_b.dir / 'synth.fifo'
client_sink_module = lab_b.pa('load-module', 'module-null-sink', f'sink_name={client_sink}', 'rate=48000', 'channels=2')
synth_module = lab_b.pa('load-module', 'module-pipe-source', f'source_name={synth}', f'file={synth_fifo}', 'format=s16le', 'rate=48000', 'channels=1')
os.environ['PULSE_SERVER'] = lab_b.server   # FreeRDP's pulse backends use lab B only
stop_feed = threading.Event()


def feed_synth():
    fd = os.open(synth_fifo, os.O_WRONLY | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOFOLLOW)
    i = 0
    try:
        while not stop_feed.is_set():
            pcm = array.array('h', [int(12000 * math.sin(2 * math.pi * 440 * (i + n) / 48000)) for n in range(480)]).tobytes()
            try:
                os.write(fd, pcm)
            except BlockingIOError:
                pass
            i += 480; time.sleep(0.01)
    finally:
        os.close(fd)


feeder = threading.Thread(target=feed_synth, daemon=True); feeder.start()


def run_case(name):
    prefix = f'mios-rdp-test-{name}-{pid}'
    mic_prefix = f'mios-rdp-test-mic-{name}-{pid}'
    speaker_on = name not in ('speaker-disabled', 'mic-restart')
    mic_on = name in ('restart', 'mic-restart', 'restart-norecovery', 'default-priority')
    native = {'audio_server': lab_a.server, 'audio_recovery_attempts': 0 if name == 'restart-norecovery' else 60,
              'audio_recovery_interval_ms': 250}
    case = root / name; case.mkdir()
    e.ensure_cert(str(case))
    mon = e.mons()[0]
    e.write_config(str(case), port=args.port, mode='mirror', runtime_dir=e.RT,
                   hypr_instance=inst['instance'], primary_output=mon['name'], operator_uid=uid, target_hz=30, min_hz=15,
                   redirect={'audio_output': speaker_on, 'microphone': mic_on, 'camera': False, 'drives': False}, native=native,
                   naming={'speaker_sink_prefix': prefix, 'microphone_source_prefix': mic_prefix})
    if args.pulse_namespace:
        wrapper = case / 'server-ns.sh'
        wrapper.write_text('#!/bin/sh\nexec unshare --user --map-root-user --mount sh -c '
                           f"'mount --bind \"$0\" \"$1\" && shift && exec unshare --user --map-user={uid} --map-group={os.getgid()} \"$@\"' "
                           f'{lab_a.run / "pulse"} /run/user/{uid}/pulse {os.environ["MIOS_RDP_BIN"]} "$@"\n')
        wrapper.chmod(0o700)
        e.BIN = str(wrapper)
    s = e.Session(str(case), name, port=args.port)
    res = {'case': name}
    caps = []
    try:
        if name == 'default-priority':
            # No configured default: WirePlumber ranks by priority, as after the operator's device vanishes.
            for key in ('default.configured.audio.sink', 'default.configured.audio.source'):
                subprocess.run(['pw-metadata', '-n', 'default', '0', key, '-d'], env=lab_a.env, capture_output=True, timeout=6)
            # pw-metadata -d deletes the key; keep it deleted for the whole case
            res['configured_defaults_cleared'] = True
        s.start_server(); s.start_xvfb('640x480')
        client_args = ['/sec:tls', '/size:640x480', '-clipboard', '-grab-keyboard', '-grab-mouse', f'/sound:sys:pulse,dev:{client_sink}']
        if mic_on:
            client_args.append(f'/microphone:sys:pulse,dev:{synth}')
        s.start_client(client_args)
        assert s.wait_attached(), 'private FreeRDP client did not attach'
        if name == 'speaker-disabled':
            time.sleep(3)
            st = s.stats().get('speaker', {})
            assert not st.get('native_ready'), st
            assert prefix not in lab_a.pa('list', 'short', 'sinks'), 'disabled permission created a sink'
            cap = Capture(lab_b, client_sink + '.monitor'); caps.append(cap)
            level, n = rms(cap.read(2.0))
            assert level < 50, f'client received audio while disabled: rms {level}'
            res.update(native_sink_created=False, client_rms=round(level, 2), client_samples=n, passed=True)
            return res
        sink = None
        if speaker_on:
            st = wait_for(lambda: s.stats().get('speaker', {}).get('native_ready') and s.stats()['speaker'], 10,
                          'speaker sink never became ready: ' + Path(s.out + '/server.log').read_text()[-2000:])
            sink = st['sink']
            assert sink.startswith(prefix) and sink in lab_a.pa('list', 'short', 'sinks'), st
            res['speaker_format'] = st.get('format')

        def speaker_roundtrip(label):
            cap = Capture(lab_b, client_sink + '.monitor'); caps.append(cap)
            quiet, _ = rms(cap.read(0.6))
            player = play(lab_a, sink, tone(2.0))
            data = cap.read(3.0)
            player.wait(10); cap.close()
            level, n = rms(data)
            assert n >= 48000 and level > 1000, f'{label}: client got samples={n} rms={level:.1f} (silence before {quiet:.1f})'
            return {'client_samples': n, 'client_rms': round(level, 2), 'client_rms_before_tone': round(quiet, 2)}

        if speaker_on:
            res['speaker'] = speaker_roundtrip('speaker')
        if mic_on:
            mic = wait_for(lambda: s.stats().get('microphone', {}).get('native_ready') and s.stats()['microphone'], 10, 'microphone never ready')
            source = mic['source']

            def mic_roundtrip(label):
                cap = Capture(lab_a, source, channels=1); caps.append(cap)
                level, n = rms(cap.read(2.0))
                cap.close()
                assert n >= 9600 and level > 1000, f'{label}: server source samples={n} rms={level:.1f}'
                return {'samples': n, 'rms': round(level, 2)}

            res['microphone_before_restart'] = mic_roundtrip('microphone before restart')
            if name == 'default-priority':
                time.sleep(1.5)
                now = lab_a.defaults()
                assert now == ('lab-default-speaker', 'lab-default-mic'), f'session node became the default: {now}'
                res.update(defaults_while_connected=now, passed=True)
                return res
            lab_a.restart_pulse()
            restart_at = time.monotonic()
            if name == 'restart-norecovery':
                time.sleep(4)
                stats = s.stats()
                assert source not in lab_a.pa('list', 'short', 'sources'), 'source reappeared without recovery'
                assert sink not in lab_a.pa('list', 'short', 'sinks'), 'sink reappeared without recovery'
                assert not stats['microphone']['native_ready'] and not stats.get('speaker', {}).get('native_ready'), stats
                res.update(recovered=False, microphone=stats['microphone'], speaker_stats=stats['speaker'], passed=True)
                return res
            wait_for(lambda: s.stats()['microphone'].get('recoveries', 0) >= 1 and s.stats()['microphone']['native_ready']
                     and source in lab_a.pa('list', 'short', 'sources'), 15, 'microphone source not recreated after pipewire-pulse restart')
            if speaker_on:
                wait_for(lambda: s.stats()['speaker'].get('recoveries', 0) >= 1 and s.stats()['speaker']['native_ready']
                         and sink in lab_a.pa('list', 'short', 'sinks'), 15, 'speaker sink not recreated after pipewire-pulse restart')
                assert s.stats()['speaker']['sink'] == sink, 'speaker name changed across recovery'
            res['recovery_seconds'] = round(time.monotonic() - restart_at, 2)
            assert s.stats()['microphone']['source'] == source, 'microphone name changed across recovery'
            res['microphone_after_restart'] = mic_roundtrip('microphone after restart')
            if speaker_on:
                res['speaker_after_restart'] = speaker_roundtrip('speaker after restart')
        stats = s.stats()
        if speaker_on:
            assert stats['speaker']['waves_sent'] > 0 and stats['speaker']['bytes_forwarded'] > 0, stats['speaker']
        res['speaker_stats'] = stats.get('speaker'); res['microphone'] = stats.get('microphone')
        res['device_forwarding'] = stats.get('device_forwarding', {}).get('audio_output')
        s.server.send_signal(signal.SIGTERM)
        assert s.server.wait(timeout=15) == 0, 'server must shut down cleanly while the client is attached'
        res['attached_server_signal_cleanup'] = True
        res['passed'] = True
        return res
    finally:
        for cap in caps:
            cap.close()
        s.stop()
        if name == 'default-priority':
            lab_a.pa('set-default-sink', 'lab-default-speaker'); lab_a.pa('set-default-source', 'lab-default-mic')
        deadline = time.monotonic() + 8
        while (prefix in lab_a.pa('list', 'short', 'sinks') or mic_prefix in lab_a.pa('list', 'short', 'sources')) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert prefix not in lab_a.pa('list', 'short', 'sinks'), 'session speaker sink leaked'
        assert mic_prefix not in lab_a.pa('list', 'short', 'sources'), 'session microphone source leaked'
        assert not any((Path(e.RT) / 'mios-rdp').glob(prefix + '*')), 'speaker FIFO directory leaked'


results = []
try:
    for name in [c for c in args.cases.split(',') if c]:
        try:
            results.append(run_case(name))
        except AssertionError as exc:
            results.append({'case': name, 'passed': False, 'error': str(exc)[:4000]})
finally:
    stop_feed.set(); feeder.join(2)
    try:
        lab_b.pa('unload-module', synth_module); lab_b.pa('unload-module', client_sink_module)
    except subprocess.SubprocessError:
        pass
    lab_ok = (lab_a.defaults(), lab_b.defaults()) == (defaults_a, defaults_b) == (('lab-default-speaker', 'lab-default-mic'),) * 2
    lab_a.stop(); lab_b.stop()
operator_after = (operator_pa('get-default-sink'), operator_pa('get-default-source'))
operator_untouched = operator_before == operator_after and not any(
    p in operator_pa('list', 'short', 'sinks') for p in ('mios-rdp-test-', 'mios-rdp-client-'))
report = {'passed': all(r.get('passed') for r in results) and lab_ok and operator_untouched, 'binary': os.environ['MIOS_RDP_BIN'],
          'results': results, 'lab_defaults_unchanged': lab_ok, 'operator_audio_untouched': operator_untouched,
          'physical_devices_used': False, 'nested_descriptor': args.nested_descriptor, 'work_dir': str(root)}
Path(args.report).write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps({'passed': report['passed'], 'cases': {r['case']: r.get('passed') for r in results}}), flush=True)
if not args.keep_work:
    shutil.rmtree(root, ignore_errors=True)
sys.exit(0 if report['passed'] else 1)
