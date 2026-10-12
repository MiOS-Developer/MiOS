#!/usr/bin/env python3
# AI-hint: Exercise the model-bake readiness gate with complete and planted incomplete model sets, without host writes or downloads.
# AI-related: automation/73-model-prep.sh, usr/share/mios/mios.toml [llamacpp]
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class TestModelBakeReady(unittest.TestCase):
    def run_bake(self, spec, present=()):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "automation/73-model-prep.sh"
            script.parent.mkdir()
            common = script.parent / "lib/common.sh"
            common.parent.mkdir()
            common.write_text("mios_log(){ echo \"$*\"; }; mios_ok(){ echo \"$*\"; }; "
                              "mios_skip(){ echo \"$*\"; }; mios_warn(){ echo \"$*\"; }; mios_err(){ echo \"$*\"; };\n")
            # Relocate every fixed FHS path in the fixture; execute the actual gate.
            source = (ROOT / "automation/73-model-prep.sh").read_text()
            for prefix in ("/usr/share/mios", "/var/lib/mios", "/usr/lib/mios"):
                source = source.replace(prefix, str(root / prefix.lstrip("/")))
            script.write_text(source)
            models = root / "models"
            models.mkdir()
            (root / "usr/share/mios/vllm").mkdir(parents=True)
            (models / ".ready").touch()  # stale readiness must not survive failure
            for name in present:
                (models / name).write_bytes(b"GGUF fixture")
            binary = root / "bin"
            binary.mkdir()
            curl = binary / "curl"
            curl.write_text("#!/bin/bash\nprintf 'DEVLOOP-PLANTED-DOWNLOAD-FAILURE\\n' >&2\nexit 22\n")
            curl.chmod(0o755)
            result = subprocess.run(["bash", str(script)], capture_output=True, text=True,
                                    env={**os.environ, "PATH": str(binary) + os.pathsep + os.environ["PATH"],
                                         "MIOS_LLAMACPP_MODELS_DIR": str(models),
                                         "MIOS_LLAMACPP_BAKE_MODELS": spec, "MIOS_VLLM_BAKE_MODEL": ""})
            return result, (models / ".ready").exists()

    def test_complete_required_set_is_ready(self):
        result, ready = self.run_bake("head.gguf=org/repo:head,embed.gguf=org/repo:embed",
                                      ("head.gguf", "embed.gguf"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(ready)

    def test_embedding_only_cannot_certify_missing_head(self):
        result, ready = self.run_bake("head.gguf=org/repo:head,embed.gguf=org/repo:embed", ("embed.gguf",))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DEVLOOP-PLANTED-DOWNLOAD-FAILURE", result.stderr)
        self.assertIn("Incomplete GGUF bake: 1/2", result.stdout)
        self.assertFalse(ready)

    def test_malformed_required_entry_cannot_certify_ready(self):
        result, ready = self.run_bake("DEVLOOP-PLANTED-MALFORMED,embed.gguf=org/repo:embed", ("embed.gguf",))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Malformed entry", result.stdout)
        self.assertFalse(ready)


if __name__ == "__main__":
    unittest.main()
