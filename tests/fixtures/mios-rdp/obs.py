#!/usr/bin/env python3
# AI-hint: Opt-in installed-libobs/PipeWire camera consumer for synthetic native RDP fixtures; portrait NV12 negative and packed BGRA positive copy controls run in isolated process groups with core dumps disabled. Saves no video.
# AI-related: src/mios-rdp-server/crates/mios-rdp-server/src/remote_camera.rs, tests/fixtures/mios-rdp/camera.py, usr/share/doc/mios/remote-camera.md
# AI-functions: main, consume, controls
import argparse
import ctypes as C
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import signal
import subprocess
import sys
import time


def consume(args):
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    name = args.consume or 'mios-obs-synthetic-' + str(os.getpid())
    assert re.fullmatch(r'[A-Za-z0-9_.-]+', name)
    assert 16 <= args.width <= 1920 and 16 <= args.height <= 1920
    provider = None
    if not args.consume:
        provider = subprocess.Popen([
            'gst-launch-1.0', '-q', 'videotestsrc', 'is-live=true', 'pattern=ball', '!',
            f'video/x-raw,format={args.format},width={args.width},height={args.height},framerate=30/1', '!',
            'pipewiresink', 'mode=provide', 'sync=false', 'async=false',
            f'stream-properties=props,node.name=(string){name},node.description=(string)Test-Camera,media.class=(string)Video/Source',
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(.3)
    try:
        obs = C.CDLL('libobs.so.30', mode=C.RTLD_GLOBAL)
        glib = C.CDLL('libglib-2.0.so.0')
        wayland = C.CDLL('libwayland-client.so.0')
        ptr, char = C.c_void_p, C.c_char_p

        def bind(lib, name, ret, argv):
            fn = getattr(lib, name)
            fn.restype, fn.argtypes = ret, argv
            return fn

        display = bind(wayland, 'wl_display_connect', ptr, [char])(os.environ.get('WAYLAND_DISPLAY', 'wayland-1').encode())
        assert display, 'A live operator Wayland display is required'
        bind(obs, 'obs_set_nix_platform', None, [C.c_int])(2)
        bind(obs, 'obs_set_nix_platform_display', None, [ptr])(display)
        assert bind(obs, 'obs_startup', C.c_bool, [char, char, ptr])(b'en-US', None, None)

        # Public libobs32.1.1 ABI: obs.h and media-io/video-io.h.
        class VideoInfo(C.Structure):
            _fields_ = [('graphics_module', char), ('fps_num', C.c_uint32), ('fps_den', C.c_uint32),
                        ('base_width', C.c_uint32), ('base_height', C.c_uint32),
                        ('output_width', C.c_uint32), ('output_height', C.c_uint32),
                        ('output_format', C.c_int), ('adapter', C.c_uint32), ('gpu_conversion', C.c_bool),
                        ('colorspace', C.c_int), ('range', C.c_int), ('scale_type', C.c_int)]

        class FrameHead(C.Structure):
            _fields_ = [('data', ptr * 8), ('linesize', C.c_uint32 * 8), ('width', C.c_uint32),
                        ('height', C.c_uint32), ('timestamp', C.c_uint64)]

        video = VideoInfo(b'/usr/lib64/libobs-opengl.so.30', 30, 1, 320, 240, 320, 240, 7, 0, False, 3, 2, 0)
        assert bind(obs, 'obs_reset_video', C.c_int, [C.POINTER(VideoInfo)])(C.byref(video)) == 0
        module = ptr()
        assert bind(obs, 'obs_open_module', C.c_int, [C.POINTER(ptr), char, char])(
            C.byref(module), b'/usr/lib64/obs-plugins/linux-pipewire.so',
            b'/usr/share/obs/obs-plugins/linux-pipewire') == 0
        assert bind(obs, 'obs_init_module', C.c_bool, [ptr])(module)
        bind(obs, 'obs_post_load_modules', None, [])()
        settings = bind(obs, 'obs_data_create_from_json', ptr, [char])(json.dumps({
            'device_id': name, 'format': json.dumps({'encoded': False,
                'video_format': 12 if args.format == 'BGRA' else 23, 'width': args.width, 'height': args.height}),
        }).encode())
        source = bind(obs, 'obs_source_create_private', ptr, [char, char, ptr])(
            b'pipewire-camera-source', b'MiOS synthetic camera consumer', settings)
        assert source
        bind(obs, 'obs_data_release', None, [ptr])(settings)
        bind(obs, 'obs_source_inc_showing', None, [ptr])(source)
        iterate = bind(glib, 'g_main_context_iteration', C.c_bool, [ptr, C.c_bool])
        get_frame = bind(obs, 'obs_source_get_frame', ptr, [ptr])
        release = bind(obs, 'obs_source_release_frame', None, [ptr, ptr])
        frames = set()
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            for _ in range(20):
                if not iterate(None, False):
                    break
            frame = get_frame(source)
            if frame:
                try:
                    head = C.cast(frame, C.POINTER(FrameHead)).contents
                    assert (head.width, head.height) == (args.width, args.height)
                    # This OBS plugin leaves timestamps at zero. Check the
                    # copied synthetic pixels, keeping no image or digest.
                    if args.format == 'BGRA':
                        assert head.linesize[0] == args.width * 4 and head.data[0]
                        frames.add(hashlib.sha256(C.string_at(head.data[0],args.width*args.height*4)).digest())
                finally:
                    release(source, frame)
            if len(frames) >= 12:
                break
            time.sleep(.015)
        assert len(frames) >= 12, ('OBS did not receive twelve distinct frames', len(frames))
        print('MIOS_OBS_NATIVE_CONSUMER ' + json.dumps({'passed': True, 'native_format': args.format,
              'frames': len(frames), 'width': args.width, 'height': args.height, 'video_recorded': False}), flush=True)
        bind(obs, 'obs_source_dec_showing', None, [ptr])(source)
        bind(obs, 'obs_source_release', None, [ptr])(source)
        bind(obs, 'obs_shutdown', None, [])()
        bind(wayland, 'wl_display_disconnect', None, [ptr])(display)
    finally:
        if provider:
            provider.terminate()
            provider.wait(timeout=3)


def controls(args):
    report = Path(args.report)
    assert not report.exists(), 'Preserve an existing report'
    results = []
    for fmt in ('NV12', 'BGRA'):
        log = report.with_name(report.stem + '-' + fmt.lower() + '.log')
        assert not log.exists()
        with log.open('w') as output:
            process = subprocess.Popen([sys.executable, __file__, '--format', fmt, '--width', '720', '--height', '1280'],
                                       stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                code = process.wait(timeout=12)
            finally:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        text = log.read_text()
        results.append({'format': fmt, 'exit_code': code, 'frames_verified': 'MIOS_OBS_NATIVE_CONSUMER' in text})
    assert results[0]['exit_code'] != 0 and not results[0]['frames_verified'], ('NV12 negative control changed', results)
    assert results[1]['exit_code'] == 0 and results[1]['frames_verified'], results
    report.write_text(json.dumps({'passed': True, 'cases': results, 'video_recorded': False}, indent=2) + '\n')
    print('MIOS_OBS_COPY_CONTROLS ' + json.dumps(results), flush=True)


def main():
    parser = argparse.ArgumentParser(description='Opt-in native OBS camera copy test; saves no camera frames')
    parser.add_argument('--consume', help='Existing synthetic node supplied by the native RDP fixture')
    parser.add_argument('--format', choices=('BGRA', 'NV12'), default='BGRA')
    parser.add_argument('--width', type=int, default=720)
    parser.add_argument('--height', type=int, default=1280)
    parser.add_argument('--controls', action='store_true')
    parser.add_argument('--report')
    args = parser.parse_args()
    assert os.getuid() != 0 and os.environ.get('MIOS_RDP_CAMERA_LIVE') == '1', 'Opt in as the desktop operator'
    if args.controls:
        assert args.report
        controls(args)
    else:
        consume(args)


if __name__ == '__main__':
    main()
