#!/usr/bin/env python3
# AI-hint: Opt-in live RDP generic-USB (MS-RDPEUSB/URBDRC) check: a private test-auth mios-rdp-server in an owned nested MiOS session against a real FreeRDP client whose /usb filter matches no device (no physical device is ever opened or claimed); proves URBDRC negotiation, per-client capability status, the per-session target, honest vhci_hcd reporting, and negative controls (server off, client without URBDRC, optional pre-fix binary).
# AI-related: src/mios-rdp-server/crates/mios-rdp-server/src/remote_usb.rs, tests/fixtures/mios-rdp/e2e.py, tests/fixtures/mios-rdp/storage.py, tools/native/mios-gen/src/device_forwarding.rs, usr/share/doc/mios/remote-usb.md
# AI-functions: usb_fds, modules, run_case, main
# Run as the desktop operator: MIOS_RDP_NATIVE_LIVE=1 MIOS_RDP_BIN=<private test-auth build> usb.py --nested-descriptor <owned session.json> --report <report.json> [--gen <mios-gen>] [--pre-fix-bin <old test-auth build>]

import argparse
import importlib.util
import json
import os
import shutil
from pathlib import Path
import subprocess
import tempfile
import time

NO_DEVICE = 'dead:beef'  # a VID:PID no real device carries: FreeRDP loads URBDRC but opens nothing
LOG = 'info,ironrdp_dvc=debug,ironrdp_server=info,ironrdp_acceptor=warn,ironrdp_connector=warn'
REPO = Path(__file__).resolve().parents[3]  # the tree whose SSOT the candidate resolves (never the live root)
PRODUCTION = Path('/usr/libexec/mios/rdp-native/mios-rdp-server')


def parse_args():
    ap = argparse.ArgumentParser(description='Opt-in live MS-RDPEUSB check; use an owned nested desktop and a private test-auth binary')
    ap.add_argument('--nested-descriptor', required=True)
    ap.add_argument('--report', required=True)
    ap.add_argument('--port', type=int, default=18477)
    ap.add_argument('--gen', help='mios-gen candidate for the per-session target selection (device-forwarding select)')
    ap.add_argument('--pre-fix-bin', help='pre-fix test-auth build: the same positive case must fail on it')
    return ap.parse_args()


def usb_fds(pid):
    """Open /dev/bus/usb nodes of a process: a claimed or merely opened physical device."""
    found = []
    try:
        for fd in os.listdir(f'/proc/{pid}/fd'):
            try:
                target = os.readlink(f'/proc/{pid}/fd/{fd}')
            except OSError:
                continue
            if target.startswith('/dev/bus/usb'):
                found.append(target)
    except OSError:
        pass
    return found


def modules():
    return sorted(l.split()[0] for l in Path('/proc/modules').read_text().splitlines() if l.split()[0] in ('vhci_hcd', 'usbip_core', 'usbip_host'))


def urbdrc_declined(log):
    return any('declined by client' in l and 'URBDRC' in l for l in log.splitlines())


def run_case(e, desc, root, name, binary, server_usb, client_usb, port, gen=None):
    case = root / name
    case.mkdir()
    e.ensure_cert(str(case))
    e.BIN = binary
    mon = e.mons()[0]
    e.write_config(str(case), port=port, mode='mirror', runtime_dir=e.RT, operator_uid=os.getuid(),
                   hypr_instance=desc['instance']['instance'], primary_output=mon['name'], target_hz=30, min_hz=15,
                   redirect={'usb': server_usb, 'microphone': False, 'drives': False, 'camera': False},
                   native={'usb_helper_socket': str(case / 'no-helper.sock'), 'usb_attach_timeout_ms': 2000},
                   device_forwarding={'targets_file': 'mios/device-forwarding/targets.json', 'default_target': 'auto',
                                      'usb': {'allow': [NO_DEVICE], 'deny': ['class:09', 'class:03', 'class:e0'], 'max_devices': 4}})
    s = e.Session(str(case), 'usb', port=port, server_env={'MIOS_RDP_LOG': LOG})
    res = {'case': name, 'server_usb': server_usb, 'client_usb': client_usb, 'binary': binary, 'usb': {}, 'attached': False}
    try:
        try:
            s.start_server()
        except SystemExit as err:  # e2e.Session reports a server that never listened this way
            res['start_error'] = str(err)[-500:]
            return res
        s.start_xvfb('640x480')
        args = ['/sec:tls', '/size:640x480', '-clipboard', '-grab-keyboard', '-grab-mouse']
        if client_usb:
            args.append(f'/usb:id:{NO_DEVICE}')
        s.start_client(args)
        res['attached'] = bool(s.wait_attached())
        usb, fds = {}, []
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            fds += usb_fds(s.client.pid)
            usb = s.stats().get('device_forwarding', {}).get('usb', {})
            time.sleep(0.5)
        res['usb'] = usb
        res['client_usb_fds'] = sorted(set(fds))
        if gen and server_usb and client_usb:
            env = {k: v for k, v in os.environ.items() if k not in ('MIOS_TOML', 'MIOS_VENDOR_TOML')}
            sel = subprocess.run([gen, 'device-forwarding', 'select', '--root', str(REPO), '--session', 'rdp', '--target', 'none', '--runtime-dir', e.RT],
                                 capture_output=True, text=True, env=env, timeout=20)
            res['select_none'] = {'rc': sel.returncode, 'stderr': sel.stderr[-500:]}
            time.sleep(2.5)
            res['target_after_select'] = s.stats().get('device_forwarding', {}).get('usb', {}).get('target')
    finally:
        s.stop()
        targets = Path(e.RT) / 'mios/device-forwarding/targets.json'
        if targets.exists():
            targets.unlink()
    log = Path(s.out, 'server.log').read_text(errors='replace')
    res['urbdrc_declined'] = urbdrc_declined(log)
    res['server_panicked'] = 'panicked' in log
    res['dvc_create_responses'] = log.count('Got DVC Create Response PDU')
    return res


def main():
    args = parse_args()
    assert os.getuid() != 0, 'run this fixture as the desktop operator'
    assert os.environ.get('MIOS_RDP_NATIVE_LIVE') == '1', 'explicit live-test opt-in is required'
    binary = os.environ.get('MIOS_RDP_BIN')
    assert binary, 'MIOS_RDP_BIN must select a private test-auth binary'
    for b in filter(None, (binary, args.pre_fix_bin)):
        assert Path(b).resolve() != PRODUCTION.resolve(), 'never the production server'
    assert 1024 <= args.port <= 65535
    os.environ.setdefault('ST_BRIDGE', '/usr/libexec/mios/mios-rdp-rfb-bridge')
    spec = importlib.util.spec_from_file_location('rdp_fixture', Path(__file__).with_name('e2e.py'))
    e = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(e)
    desc = json.loads(Path(args.nested_descriptor).read_text())
    e.RT = desc['runtime']
    e.ENV = dict(os.environ, XDG_RUNTIME_DIR=e.RT, HYPRLAND_INSTANCE_SIGNATURE=desc['instance']['instance'], WAYLAND_DISPLAY=desc['instance']['wl_socket'])
    e.ENV.pop('DISPLAY', None)

    vhci_loaded = Path('/sys/devices/platform/vhci_hcd.0').exists()
    before = {'modules': modules(), 'geometry': e.local_snapshot()}
    root = Path(tempfile.mkdtemp(prefix='mios-remote-usb-live-', dir='/var/tmp'))
    results, failures = [], []

    def check(cond, msg):
        if not cond:
            failures.append(msg)

    on = run_case(e, desc, root, 'server-on-client-usb', binary, True, True, args.port, args.gen)
    results.append(on)
    u = on['usb']
    check(on['attached'], 'positive: client did not attach')
    check(u.get('implemented') is True and u.get('permitted') is True and u.get('protocol') == 'MS-RDPEUSB', f'positive: status {u}')
    check(not on['urbdrc_declined'], 'positive: FreeRDP with /usb must accept the URBDRC channel')
    check(u.get('client', {}).get('status') == 'no_devices_offered' and u.get('devices') == [], 'positive: a filter that matches nothing offers no device')
    check(u.get('endpoint', {}).get('available') is vhci_loaded, 'endpoint availability must equal the host vhci_hcd state')
    if not vhci_loaded:
        check(str(u.get('endpoint', {}).get('reason', '')).startswith('vhci_hcd_not_loaded'), 'missing module must be reported, not hidden')
    check(on['client_usb_fds'] == [], f'client opened a physical USB node: {on["client_usb_fds"]}')
    check(not on['server_panicked'], 'positive: server panicked')
    if args.gen:
        check(on.get('select_none', {}).get('rc') == 0 and on.get('target_after_select') == 'none', f'per-session target select: {on.get("select_none")} {on.get("target_after_select")}')

    ios_like = run_case(e, desc, root, 'server-on-client-without-urbdrc', binary, True, False, args.port)
    results.append(ios_like)
    check(ios_like['urbdrc_declined'], 'negative: a client without URBDRC (like iOS Windows App) must decline the channel')
    check(ios_like['usb'].get('client', {}).get('status') == 'no_devices_offered', f'negative: {ios_like["usb"]}')

    off = run_case(e, desc, root, 'server-off', binary, False, True, args.port)
    results.append(off)
    check(off['usb'].get('permitted') is False and off['usb'].get('client', {}).get('status') == 'server_disabled', f'negative: server off {off["usb"]}')
    check(not off['urbdrc_declined'], 'negative: server off must never offer URBDRC')

    pre = None
    if args.pre_fix_bin:
        pre = run_case(e, desc, root, 'pre-fix-server-on-client-usb', args.pre_fix_bin, True, True, args.port)
        results.append(pre)
        pre['fails_contract'] = pre['usb'].get('implemented') is not True
        check(pre['fails_contract'], 'pre-fix control: the old binary must not report a working USB bridge')

    after = {'modules': modules(), 'geometry': e.local_snapshot()}
    check(before['modules'] == after['modules'], f'kernel modules changed: {before["modules"]} -> {after["modules"]}')
    check(Path('/sys/devices/platform/vhci_hcd.0').exists() == vhci_loaded, 'vhci_hcd state changed')
    check(before['geometry'] == after['geometry'], 'nested display geometry changed')
    report = {'passed': not failures, 'failures': failures, 'vhci_hcd_loaded': vhci_loaded, 'modules': after['modules'],
              'no_device_filter': NO_DEVICE, 'results': results, 'work_dir': str(root)}
    Path(args.report).write_text(json.dumps(report, indent=2) + '\n')
    if not failures:
        shutil.rmtree(root, ignore_errors=True)  # keep the case logs only when a check failed
        report['work_dir'] = None
        Path(args.report).write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'passed': report['passed'], 'failures': failures}), flush=True)
    return 0 if not failures else 1


if __name__ == '__main__':
    raise SystemExit(main())
