# AI-hint: Exercise real native management/generator dispatch without retired Python scripts.
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MIOSD = os.environ.get('MIOS_TEST_MIOSD', '/usr/bin/miosd')
GEN = os.environ.get('MIOS_TEST_GEN', '/usr/bin/mios-gen')

class NativeGeneratorDispatch(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mios-native-dispatch-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        vendor = self.root / 'usr/share/mios'
        vendor.mkdir(parents=True)
        shutil.copy2(ROOT / 'usr/share/mios/mios.toml', vendor / 'mios.toml')
        shutil.copytree(ROOT / 'usr/share/containers/systemd', self.root / 'usr/share/containers/systemd')
        self.env = {k: v for k, v in os.environ.items() if not k.startswith('MIOS_')}
        self.env.update(MIOS_ROOT=str(self.root), MIOS_GEN_BIN=GEN, MIOS_USER_TOML=str(self.root/'no-user.toml'))

    def run_tool(self, verb, *args):
        return subprocess.run([MIOSD, verb, *args], env=self.env, text=True, capture_output=True, timeout=60)

    def test_quadlets_render_and_real_drift_is_rejected(self):
        result = self.run_tool('generate-quadlets')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.root/'tools/generate-pod-quadlets.py').exists())
        result = self.run_tool('generate-quadlets', '--check')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        unit = self.root/'usr/share/containers/systemd/mios-headscale.container'
        with unit.open('a') as stream: stream.write('\n# DEVLOOP-PLANTED-QUADLET-DRIFT\n')
        self.assertNotEqual(self.run_tool('generate-quadlets', '--check').returncode, 0)
        self.assertIn('DEVLOOP-PLANTED-QUADLET-DRIFT', unit.read_text())

    def test_missing_generator_fails_without_creating_policy(self):
        self.env['MIOS_GEN_BIN'] = str(self.root/'absent-native-generator')
        result = self.run_tool('cosign-policy')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('native mios-gen', result.stderr)
        self.assertFalse((self.root/'usr/lib/containers/policy.json').exists())

    def test_policy_renders_without_retired_python_generator(self):
        result = self.run_tool('cosign-policy')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.root/'usr/lib/containers/policy.json').is_file())
        self.assertFalse((self.root/'tools/generate-cosign-policy.py').exists())
        result = self.run_tool('cosign-policy', '--check')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_layered_host_policy_reaches_native_outputs(self):
        host = self.root/'etc/mios/mios.toml'
        host.parent.mkdir(parents=True)
        host.write_text('[security.sigstore]\npolicy_mode="reject"\n', encoding='utf-8')
        result = self.run_tool('cosign-policy')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        policy = self.root/'usr/lib/containers/policy.json'
        self.assertIn('"reject"', policy.read_text())
        host.write_text('[security.sigstore\n', encoding='utf-8')
        result = self.run_tool('cosign-policy')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('"reject"', policy.read_text())

if __name__ == '__main__': unittest.main()
