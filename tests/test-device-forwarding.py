#!/usr/bin/env python3
# AI-hint: Contract test for generic USB forwarding: SSOT [remote.device_forwarding] + [remote.rdp.redirect].usb defaults, unit projections of the vhci attach helper, configurator coverage, native.json/.rdp projection, the mios-gen device-forwarding report/select/spice-filter verbs on a fake sysfs, the server's --print-defaults parity, and negative controls proving the pre-fix tree fails the same checks.
# AI-related: usr/share/mios/mios.toml, src/mios-rdp-server/crates/mios-rdp-server/src/remote_usb.rs, src/mios-rdp-server/crates/mios-rdp-priv/src/main.rs, tools/native/mios-gen/src/device_forwarding.rs, usr/libexec/mios/mios-rdp, usr/share/doc/mios/remote-usb.md
# AI-functions: Ssot, Projections, GenVerbs, ServerDefaults, PreFixNegative
# Never claims a device, loads a module or touches a VM: probes run against a scratch sysfs tree and libvirt is skipped.
# MIOS_GEN_BIN / MIOS_RDP_SERVER_BIN select candidate binaries; MIOS_DF_PRE_FIX names the pre-fix commit (default: main's merge base).
import json, os, shutil, subprocess, tempfile, tomllib, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SSOT = ROOT / 'usr/share/mios/mios.toml'
GEN = os.environ.get('MIOS_GEN_BIN') or shutil.which('mios-gen')
SERVER = os.environ.get('MIOS_RDP_SERVER_BIN', '')
UNIT_GEN = shutil.which('mios-unit-gen')
PRE_FIX = os.environ.get('MIOS_DF_PRE_FIX', 'bcd6b3f22')
ENV = {k: v for k, v in os.environ.items() if k not in ('MIOS_TOML', 'MIOS_VENDOR_TOML')}


def ssot_checks(doc, server_src, rdp_src):
    """The contract, as a list of failures (empty = pass); reused verbatim against the pre-fix tree."""
    bad = []
    remote = doc.get('remote', {})
    df = remote.get('device_forwarding')
    if not isinstance(df, dict):
        return ['[remote.device_forwarding] missing']
    usb = df.get('usb', {})
    if remote.get('rdp', {}).get('redirect', {}).get('usb') is not False:
        bad.append('[remote.rdp.redirect].usb must default to false')
    if usb.get('allow') != []:
        bad.append('usb.allow must default to empty (forward nothing)')
    for c in ('class:03', 'class:09'):
        if c not in usb.get('deny', []):
            bad.append(f'usb.deny must contain {c}')
    if not 1 <= usb.get('max_devices', 0) <= 16:
        bad.append('usb.max_devices out of range')
    if df.get('default_target') not in ('auto', 'none'):
        bad.append('default_target')
    if not any(g.startswith('mios-xbox') for g in df.get('exclude_domains', [])):
        bad.append('the mios-xbox VMs must be excluded from every probe')
    if df.get('libvirt_uris') != ['qemu:///session']:
        bad.append('libvirt inventory must default to the session URI only')
    n = remote.get('rdp', {}).get('native', {})
    for k in ('usb_helper_socket', 'usb_attach_timeout_ms', 'usb_max_transfer_kib', 'usb_max_inflight'):
        if k not in n:
            bad.append(f'[remote.rdp.native].{k} missing')
    units = doc.get('units', {})
    svc = units.get('mios-rdp-usb@.service', {}).get('Service', {})
    if svc.get('ProtectKernelModules') != 'yes':
        bad.append('USB helper must keep ProtectKernelModules=yes')
    if svc.get('ProtectKernelTunables') == 'yes':
        bad.append('USB helper needs a writable /sys for vhci attach')
    if '--usb' not in svc.get('ExecStart', ''):
        bad.append('USB helper must run mios-rdp-priv --usb')
    if 'with_usb_factory' not in server_src:
        bad.append('server never registers a URBDRC device factory')
    if '"native_usb_bridge_pending"' in server_src:
        bad.append('server still reports the USB bridge as pending')
    if 'device_forwarding' not in rdp_src or 'usbdevicestoredirect' not in rdp_src:
        bad.append('mios-rdp does not project device_forwarding / usbdevicestoredirect')
    return bad


def git_show(rev, path):
    r = subprocess.run(['git', '-C', str(ROOT), 'show', f'{rev}:{path}'], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ''


class Ssot(unittest.TestCase):
    def test_contract(self):
        doc = tomllib.loads(SSOT.read_text())
        server = (ROOT / 'src/mios-rdp-server/crates/mios-rdp-server/src/main.rs').read_text()
        rdp = (ROOT / 'usr/libexec/mios/mios-rdp').read_text()
        self.assertEqual(ssot_checks(doc, server, rdp), [])

    def test_configurator_exposes_every_new_key(self):
        html = (ROOT / 'usr/share/mios/configurator/mios.html').read_text()
        for key in ('remote.rdp.redirect.usb', 'remote.device_forwarding.usb.allow', 'remote.device_forwarding.usb.deny',
                    'remote.device_forwarding.usb.max_devices', 'remote.device_forwarding.default_target',
                    'remote.device_forwarding.libvirt_uris', 'remote.device_forwarding.exclude_domains',
                    'remote.rdp.native.usb_attach_timeout_ms', 'remote.rdp.native.usb_max_transfer_kib',
                    'remote.rdp.native.usb_max_inflight', 'remote.rdp.native.usb_helper_socket'):
            self.assertIn(f'data-key="{key}"', html, key)

    def test_preset_enables_the_helper_socket(self):
        self.assertIn('enable mios-rdp-usb.socket', (ROOT / 'usr/lib/systemd/system-preset/90-mios.preset').read_text())


@unittest.skipUnless(UNIT_GEN, 'mios-unit-gen not installed')
class Projections(unittest.TestCase):
    def test_units_are_faithful_projections(self):
        for unit in ('mios-rdp-usb.socket', 'mios-rdp-usb@.service'):
            r = subprocess.run([UNIT_GEN, '--render', unit, '--root', str(ROOT)], capture_output=True, text=True, env=ENV)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout, (ROOT / 'usr/lib/systemd/system' / unit).read_text(), unit)

    def test_native_json_and_rdp_file(self):
        with tempfile.TemporaryDirectory() as d:
            env = dict(ENV, MIOS_VENDOR_TOML=str(SSOT), MIOS_TOML_GET=str(ROOT / 'usr/libexec/mios/mios-toml-get'))
            r = subprocess.run([str(ROOT / 'usr/libexec/mios/mios-rdp'), 'native-config', d], capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            nj = json.loads(Path(d, 'native.json').read_text())
            self.assertEqual(nj['device_forwarding']['usb']['allow'], [])
            self.assertIs(nj['redirect']['usb'], False)
            off = subprocess.run([str(ROOT / 'usr/libexec/mios/mios-rdp'), 'rdp-file'], capture_output=True, text=True, env=env)
            self.assertEqual(off.returncode, 0, off.stderr)
            self.assertIn('full address:s:', off.stdout)
            self.assertNotIn('usbdevicestoredirect', off.stdout, 'USB must not be requested while the server forbids it')
            on_toml = Path(d, 'on.toml')
            on_toml.write_text(SSOT.read_text().replace('usb                 = false         # MS-RDPEUSB', 'usb                 = true          # MS-RDPEUSB', 1))
            on = subprocess.run([str(ROOT / 'usr/libexec/mios/mios-rdp'), 'rdp-file'], capture_output=True, text=True, env=dict(env, MIOS_VENDOR_TOML=str(on_toml)))
            self.assertEqual(on.returncode, 0, on.stderr)
            self.assertIn('usbdevicestoredirect:s:*', on.stdout)


@unittest.skipUnless(GEN and os.access(GEN, os.X_OK), 'mios-gen not built')
class GenVerbs(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='mios-df-'))
        self.sys = self.tmp / 'sys'
        (self.sys / 'lib-modules').mkdir(parents=True)
        (self.sys / 'lib-modules/modules.dep').write_text('kernel/drivers/usb/usbip/usbip-core.ko.xz:\n')
        self.rt = self.tmp / 'rt'
        self.rt.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def gen(self, *args):
        r = subprocess.run([GEN, 'device-forwarding', *args, '--root', str(ROOT), '--runtime-dir', str(self.rt)], capture_output=True, text=True, env=ENV, timeout=30)
        return r

    def test_report_is_honest_about_a_missing_module(self):
        r = self.gen('report', '--sys', str(self.sys), '--no-libvirt')
        self.assertEqual(r.returncode, 0, r.stderr)
        d = json.loads(r.stdout)
        self.assertEqual(d['transports']['usbip_vhci']['vhci']['module'], 'absent')
        self.assertFalse(d['transports']['rdp_urbdrc']['available'])
        self.assertIn('usb = false', d['transports']['rdp_urbdrc']['reason'])
        self.assertIn('iOS/iPadOS', d['transports']['rdp_urbdrc']['clients'])
        self.assertEqual(d['policy']['spice_filter'].split('|')[-1], '-1,-1,-1,-1,0')
        rdp = [s for s in d['sessions'] if s['session'] == 'rdp'][0]
        self.assertEqual(rdp['effective'], 'none')
        (self.sys / 'lib-modules/modules.dep').write_text('kernel/drivers/usb/usbip/vhci-hcd.ko.xz: kernel/drivers/usb/usbip/usbip-core.ko.xz\n')
        d = json.loads(self.gen('report', '--sys', str(self.sys), '--no-libvirt').stdout)
        self.assertEqual(d['transports']['usbip_vhci']['vhci']['module'], 'available_not_loaded')
        self.assertIn('never loads', d['transports']['usbip_vhci']['vhci']['reason'])

    def test_select_and_spice_filter(self):
        self.assertEqual(self.gen('select', '--session', 'rdp', '--target', 'none').returncode, 0)
        doc = json.loads((self.rt / 'mios/device-forwarding/targets.json').read_text())
        self.assertEqual(doc['sessions']['rdp']['target'], 'none')
        self.assertEqual((self.rt / 'mios/device-forwarding/targets.json').stat().st_mode & 0o777, 0o600)
        bad = self.gen('select', '--session', 'spice:mios-xbox-current', '--target', 'none')
        self.assertNotEqual(bad.returncode, 0, 'an excluded VM is never a target')
        self.assertNotEqual(self.gen('select', '--session', 'rdp', '--target', 'spice-usbredir').returncode, 0)
        self.assertEqual(self.gen('select', '--session', 'spice:lab', '--target', 'none').returncode, 0)
        self.assertEqual(self.gen('spice-filter', '--session', 'spice:lab').stdout.strip(), '-1,-1,-1,-1,0')
        f = self.gen('spice-filter').stdout.strip()
        self.assertTrue(f.startswith('0x09,-1,-1,-1,0|0x03,-1,-1,-1,0|'), f)


@unittest.skipUnless(SERVER and os.access(SERVER, os.X_OK), 'set MIOS_RDP_SERVER_BIN to a candidate build')
class ServerDefaults(unittest.TestCase):
    def test_print_defaults_equal_ssot(self):
        d = json.loads(subprocess.run([SERVER, '--print-defaults'], capture_output=True, text=True, timeout=30).stdout)
        doc = tomllib.loads(SSOT.read_text())
        rdp = doc['remote']['rdp']
        self.assertIs(d['redirect']['usb'], rdp['redirect']['usb'])
        for k in ('usb_helper_socket', 'usb_attach_timeout_ms', 'usb_max_transfer_kib', 'usb_max_inflight'):
            self.assertEqual(d['native'][k], rdp['native'][k], k)
        df = doc['remote']['device_forwarding']
        self.assertEqual(d['device_forwarding']['usb'], df['usb'])
        self.assertEqual((d['device_forwarding']['targets_file'], d['device_forwarding']['default_target']), (df['targets_file'], df['default_target']))


class PreFixNegative(unittest.TestCase):
    """Negative control: the same contract fails on the pre-fix tree."""

    def test_pre_fix_tree_fails_the_contract(self):
        old = git_show(PRE_FIX, 'usr/share/mios/mios.toml')
        if not old:
            self.skipTest(f'{PRE_FIX} not available')
        bad = ssot_checks(tomllib.loads(old), git_show(PRE_FIX, 'src/mios-rdp-server/crates/mios-rdp-server/src/main.rs'),
                          git_show(PRE_FIX, 'usr/libexec/mios/mios-rdp'))
        self.assertTrue(bad, 'the pre-fix tree must fail the device-forwarding contract')
        self.assertFalse(git_show(PRE_FIX, 'usr/lib/systemd/system/mios-rdp-usb@.service'), 'no USB helper unit before the fix')
        self.assertNotIn('device-forwarding', git_show(PRE_FIX, 'tools/native/mios-gen/src/main.rs'))


if __name__ == '__main__':
    unittest.main()
