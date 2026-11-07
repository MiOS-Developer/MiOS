#!/usr/bin/env python3
# AI-hint: Operator opt-in synthetic RDPECAM DVC wire tests through the compiled Rust test binary and actual consumers in a private lab PipeWire: native PipeWire, installed OBS (libobs), the camera-portal picker (media.role=Camera) and a V4L2-API consumer through the mios-camera-v4l2 launcher. Verify raw, H264, MJPEG, orientation, scaling, consent denial, disabled forwarding, provider restart, pause/resume and cleanup without recording a physical camera.
# AI-related: src/mios-rdp-server/crates/mios-rdp-server/src/remote_camera.rs, usr/share/mios/mios.toml, usr/bin/mios-camera-v4l2, tests/fixtures/mios-rdp/obs.py, usr/share/doc/mios/remote-camera.md
# AI-functions: main, cases

import argparse
import atexit
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


def cases():
    # name, client format, cleanup mode, portrait, transposed SPS, larger coded frame,
    # V4L2 compatibility, kill the provider (restart budget), installed OBS consumer
    yield from (
        ('i420', 'i420', '0', '0', '0', '0', '1', None, False),
        ('h264', 'h264', '0', '0', '0', '0', '1', None, False),
        ('mjpg', 'mjpg', '1', '0', '0', '0', '1', None, False),
        ('enumeration-close', 'i420', 'enum', '0', '0', '0', '1', None, False),
        ('h264-portrait', 'h264', '0', '1', '0', '0', '1', None, True),
        ('h264-transposed', 'h264', '0', '1', '1', '0', '1', None, False),
        ('h264-scaled', 'h264', '0', '1', '0', '1', '1', None, False),
        # A killed decoder/provider restarts and republishes the same camera.
        ('h264-provider-restart', 'h264', '0', '0', '0', '0', '1', '3', False),
        # Negative controls: no restart budget closes the camera (the pre-restart
        # behaviour); camera_v4l2 off gives V4L2-only applications no camera.
        ('h264-provider-no-restart', 'h264', '0', '0', '0', '0', '1', '0', False),
        ('h264-v4l2-off', 'h264', '0', '0', '0', '0', '0', None, False),
    )


def main():
    parser = argparse.ArgumentParser(description='Synthetic RDPECAM protocol/native video integration; no physical camera capture')
    parser.add_argument('--test-binary', required=True, help='compiled Rust unit-test binary from the isolated builder')
    parser.add_argument('--report', required=True)
    parser.add_argument('--pipewire-runtime', required=True, help='private lab PipeWire runtime dir (never the operator session)')
    parser.add_argument('--v4l2-command', help='JSON argv prefix running a V4L2 consumer through mios-camera-v4l2 with camera_v4l2 = true')
    parser.add_argument('--v4l2-off-command', help='the same launcher prefix with an SSOT layer setting camera_v4l2 = false')
    parser.add_argument('--v4l2-library', help='libpw-v4l2.so path the server reports for the v4l2 capability')
    parser.add_argument('--picker-command', help='JSON argv that prints the camera portal IsCameraPresent value')
    parser.add_argument('--obs', action='store_true', help='also run the installed-libobs consumer (needs WAYLAND_DISPLAY of the lab session)')
    parser.add_argument('--only', action='append', help='run only these case names')
    args = parser.parse_args()
    assert os.getuid() != 0, 'run as the desktop operator'
    assert os.environ.get('MIOS_RDP_CAMERA_LIVE') == '1', 'explicit opt-in is required'
    runtime = Path(args.pipewire_runtime).resolve()
    assert runtime != Path(f'/run/user/{os.getuid()}'), 'camera-role test nodes stay out of the operator PipeWire graph'
    assert (runtime / 'pipewire-0').exists(), 'start the private lab PipeWire first'
    binary = Path(args.test_binary).resolve()
    assert binary.is_file() and os.access(binary, os.X_OK)
    assert binary != Path('/usr/libexec/mios/rdp-native/mios-rdp-server').resolve()
    test = 'remote_camera::tests::camera_drdynvc_to_native_video_consumer'
    listing = subprocess.check_output([str(binary), '--list'], text=True, timeout=10)
    assert test + ': test' in listing, 'the selected binary does not contain the native camera fixture'
    report = Path(args.report)
    assert not report.exists(), 'preserve an existing report'
    for value in (args.v4l2_command, args.v4l2_off_command, args.picker_command):
        if value:
            assert isinstance(json.loads(value), list)
    # Per-case logs are scratch, removed at exit: a failure carries the output and the report is the evidence.
    work = Path(tempfile.mkdtemp(prefix='mios-camera-native-', dir='/var/tmp'))
    atexit.register(shutil.rmtree, work, True)
    results = []
    for case_name, format_name, cleanup, portrait, transposed, large, v4l2, restarts, obs in cases():
        if args.only and case_name not in args.only:
            continue
        env = dict(os.environ, MIOS_RDP_CAMERA_TEST_FORMAT=format_name, MIOS_RDP_CAMERA_TEST_REMOVE=cleanup,
                   MIOS_RDP_CAMERA_TEST_PORTRAIT=portrait, MIOS_RDP_CAMERA_TEST_TRANSPOSED=transposed,
                   MIOS_RDP_CAMERA_TEST_LARGE_CODED=large, MIOS_RDP_CAMERA_TEST_V4L2=v4l2,
                   MIOS_RDP_CAMERA_TEST_RUNTIME=str(runtime), PIPEWIRE_RUNTIME_DIR=str(runtime))
        launcher = args.v4l2_command if v4l2 == '1' else args.v4l2_off_command
        optional = (('MIOS_RDP_CAMERA_TEST_V4L2_CMD', launcher), ('MIOS_RDP_CAMERA_TEST_PICKER_CMD', args.picker_command),
                    ('MIOS_RDP_CAMERA_TEST_V4L2_LIBRARY', args.v4l2_library),
                    ('MIOS_RDP_CAMERA_TEST_PROVIDER_RESTARTS', restarts),
                    ('MIOS_RDP_CAMERA_TEST_KILL_PROVIDER', '1' if restarts is not None else None),
                    ('MIOS_RDP_CAMERA_OBS_FIXTURE', str(Path(__file__).with_name('obs.py')) if obs and args.obs else None))
        for key, value in optional:
            if value:
                env[key] = value
            else:
                env.pop(key, None)
        run = subprocess.run([str(binary), '--ignored', '--exact', test, '--nocapture', '--test-threads=1'], env=env,
                             capture_output=True, text=True, timeout=180)
        (work / (case_name + '.log')).write_text(run.stdout + run.stderr)
        assert run.returncode == 0, run.stdout + run.stderr
        assert '1 passed; 0 failed' in run.stdout, 'a zero-test invocation is not verification'
        proof = [line.split('MIOS_CAMERA_NATIVE_PROOF ', 1)[1] for line in run.stdout.splitlines() if 'MIOS_CAMERA_NATIVE_PROOF ' in line]
        assert len(proof) == 1
        entry = json.loads(proof[0])
        entry.update(format=format_name, case=case_name)
        assert entry['frames'] == 12 and entry['bytes'] == 1382400
        assert entry['advertised_video_format'] and not entry['consumer_dimensions_forced']
        assert entry['native_format'] == 'BGRA' and entry['source']['native_format'] == 'BGRA'
        assert entry['permission_controls'] and entry['consumer_idle_deactivates'] and entry['consumer_reopen_resumes'] and entry['disconnect_cleanup']
        assert entry['picker_node']['media.role'] == 'Camera'
        assert entry['picker_node']['node.description'] == 'Synthetic camera (MiOS RDP)'
        if obs and args.obs:
            assert entry['installed_obs_consumer'] is True
        if args.picker_command:
            assert entry['portal_camera'] is True
        if launcher:
            if v4l2 == '1':
                assert entry['v4l2']['frames'] == 12 and entry['v4l2']['bytes'] > 0, entry['v4l2']
                if args.v4l2_library:
                    assert entry['source']['consumers']['v4l2'] is True
            else:
                assert entry['v4l2']['refused'] is True and entry['source']['consumers']['v4l2'] is False, entry['v4l2']
        if restarts == '3':
            assert entry['provider_restart']['restarts'] == 1 and entry['provider_restart']['native_after_restart'], entry['provider_restart']
        elif restarts == '0':
            assert entry['provider_restart']['camera_closed'] and entry['provider_restart']['restarts'] == 0, entry['provider_restart']
        assert len(entry['negatives']) == 2
        for negative in entry['negatives']:
            assert negative['picker_nodes'] == 0 and negative['native_frames'] == 0
            if args.picker_command:
                assert negative['portal_camera'] is False
            if launcher:
                assert negative['v4l2_frames'] == 0
        results.append(entry)
        print(json.dumps({'case': case_name, 'format': format_name, 'passed': True, 'native_frames': entry['frames'],
                          'v4l2': entry['v4l2'], 'portal_camera': entry['portal_camera'],
                          'obs': entry['installed_obs_consumer'], 'provider_restart': entry['provider_restart']}), flush=True)
    assert results, 'no case selected'
    result = {'passed': True, 'protocol': 'MS-RDPECAM over actual encoded DRDYNVC messages',
              'transport': 'in-process protocol loop, not TCP/TLS or a physical mobile client',
              'pipewire': 'private lab runtime ' + str(runtime), 'physical_camera_recorded': False,
              'cases': results}
    with report.open('x') as handle:
        json.dump(result, handle, indent=2)
        handle.write('\n')
    print(json.dumps({'passed': True, 'cases': [r['case'] for r in results], 'report': str(report)}), flush=True)


if __name__ == '__main__':
    main()
