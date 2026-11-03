#!/usr/bin/env python3
# AI-hint: Compiled CLI dispatcher checks and offline /usr/bin/mios layered AI configuration tests.
# AI-related: usr/bin/mios, usr/lib/mios/mios_toml.py, src/mios-rs/miosd/src/cli.rs

import pathlib
import subprocess
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKSPACE = ROOT / "src" / "mios-rs"

def _cargo(*args):
    """Run a cargo command in the workspace, on whatever platform we are on.

    This suite used to shell into a WSL distro by name ("podman-MiOS-DEV") and
    cd to /mnt/c/MiOS, so it could only pass on one developer's Windows box and
    failed outright on any CI runner. Skip -- loudly -- when there is no
    toolchain, so a missing cargo can never read as a passing dispatcher test.
    """
    import shutil
    if shutil.which("cargo") is None:
        raise unittest.SkipTest("cargo is not on PATH; cannot exercise the miosd CLI")
    return subprocess.run(list(args), cwd=str(WORKSPACE), capture_output=True, text=True)

class TestMiosCliDispatcher(unittest.TestCase):
    def test_cli_help_and_verbs_coverage(self):
        """Verify that CLI prints help and includes all expected verbs."""
        res = _cargo("cargo", "run", "-q", "-p", "miosd", "--", "cli", "--help")
        self.assertEqual(res.returncode, 0, f"Help failed: {res.stdout}\n{res.stderr}")

        output = res.stdout + res.stderr
        expected_verbs = [
            "build", "dash", "mini", "mon", "config", "code", "ai",
            "xbox", "virt", "vfio", "tune", "summary", "profile",
            "assess", "theme", "dotfiles", "new", "iommu", "env",
            "sync-env", "blade", "flatpaks", "user", "flight", "models",
            "update", "check", "status", "logs", "backup"
        ]
        for v in expected_verbs:
            self.assertIn(v, output, f"Verb '{v}' missing from CLI dispatcher help output")

    def test_completion_generation(self):
        """Verify that shell completions are correctly generated for bash, zsh, fish, pwsh."""
        for shell in ["bash", "zsh", "fish", "pwsh"]:
            res = _cargo("cargo", "run", "-q", "-p", "miosd", "--",
                         "cli", "--generate-completion", shell)
            self.assertEqual(res.returncode, 0, f"Completion generation failed for {shell}")
            out = res.stdout
            self.assertTrue(len(out) > 50, f"Completion output too short for {shell}")
            self.assertIn("build", out)
            self.assertIn("dash", out)

    def test_rust_unit_tests_pass(self):
        """Execute the Rust unit tests for the CLI dispatcher module."""
        res = _cargo("cargo", "test", "-p", "miosd", "--lib", "--", "cli")
        self.assertEqual(
            res.returncode, 0,
            f"Cargo test for miosd cli failed:\nstdout: {res.stdout}\nstderr: {res.stderr}"
        )
        self.assertIn("test cli::dispatcher::tests::test_known_verbs_table_count ... ok", res.stdout)
        self.assertIn("test cli::completions::tests::test_completion_generation ... ok", res.stdout)
        self.assertIn("test cli::tests::test_dispatch_help ... ok", res.stdout)

class TestMiosChatConfig(unittest.TestCase):
    """Actual shared resolver + direct CLI construction; no inference transport."""
    def setUp(self):
        import tempfile
        import os
        self.scratch = tempfile.TemporaryDirectory(prefix="mios-cli-config-")
        self.root = pathlib.Path(self.scratch.name)
        self.vendor = self.root / "usr/share/mios/mios.toml"
        self.host = self.root / "host/mios.toml"
        self.user = self.root / "user/mios.toml"
        for path in (self.vendor, self.host, self.user):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("")
        self.vendor.write_text('''[ports]\nstack_id=0\n[ports.categories.agent]\nbase=17000\nstride=2\nmembers=["gateway", "agent_pipe"]\n[ai]\nendpoint="http://127.0.0.1:${MIOS_PORTS_AGENT_PIPE}/v1"\nagent_model="Fixture Public"\nmodel="fixture-base"\napi_key="fixture-config-key"\n''')
        self.env = {key:value for key,value in os.environ.items()
                    if not key.startswith(("MIOS_", "PYTHON", "OPENAI_"))}
        self.env.update(HOME=str(self.root / "home"), PYTHONDONTWRITEBYTECODE="1",
            MIOS_TOML_ROOT=str(self.root), MIOS_VENDOR_TOML=str(self.vendor),
            MIOS_HOST_TOML=str(self.host), MIOS_USER_TOML=str(self.user),
            MIOS_VENDOR_TOML_D=str(self.vendor.parent / "mios.d"),
            MIOS_HOST_TOML_D=str(self.host.parent / "mios.d"),
            MIOS_USER_TOML_D=str(self.user.parent / "mios.d"),
            MIOS_DB_AUTHORITATIVE="0", MIOS_RESOLVER_NATIVE="0")

    def tearDown(self):
        self.scratch.cleanup()

    def run_cli(self, *, env=None, mode="config"):
        import json
        import sys
        code = r'''import importlib.machinery, importlib.util, json, sys, types
path, mode = sys.argv[1:]
loader=importlib.machinery.SourceFileLoader('mios_cli_config_fixture',path)
spec=importlib.util.spec_from_loader(loader.name,loader)
m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;loader.exec_module(m)
if mode in ('config','native'):
 endpoint,model=m._resolve_ai_config()
 key=m._resolve_api_key()
 if mode=='native':
  resolver,_,_=m._ai_configuration()
  native=resolver._native_resolver_json()
  if not isinstance(native,dict) or not isinstance(native.get('merged'),dict):
   print(json.dumps({'native_unavailable':True}));raise SystemExit(0)
 print(json.dumps({'endpoint':endpoint,'model':model,'key_present':bool(key),
                   'configured_key':key=='fixture-config-key','explicit_key':key=='fixture-explicit-key'}))
else:
 calls=[]
 sdk=types.ModuleType('openai')
 class Client:
  def __init__(self,**kwargs): calls.append(kwargs)
 sdk.OpenAI=Client;sys.modules['openai']=sdk
 plugins=types.ModuleType('cli.plugin_loader')
 plugins.get_available_plugins=lambda:{};plugins.run_plugin=lambda *a:None
 sys.modules['cli.plugin_loader']=plugins
 m.load_session=lambda:{'id':'fixture','history':[]}
 m.save_session=lambda *a:None
 m.resolve_system_prompt=lambda:''
 m.chat_streaming=lambda *a,**k:None
 rc=m.main(['mios','--help'] if mode=='help' else ['mios','An opaque request.'])
 print(json.dumps({'exit':rc,'sdk_calls':len(calls),'endpoint':calls[0]['base_url'] if calls else None,
                   'model':m.MODEL if calls else None,
                   'key_preserved':bool(calls and calls[0]['api_key']=='fixture-explicit-key')}))
'''
        result = subprocess.run([sys.executable, "-c", code, str(ROOT / "usr/bin/mios"), mode],
            env={**self.env, **(env or {})}, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        # Help may print its local command catalogue before the structured result.
        return json.loads(result.stdout.splitlines()[-1]), result

    def test_no_login_environment_expands_category_derived_port(self):
        values, _ = self.run_cli()
        self.assertEqual(values["endpoint"], "http://127.0.0.1:17002/v1")
        self.assertEqual(values["model"], "Fixture Public")
        self.assertTrue(values["configured_key"])
        values, _ = self.run_cli(mode="main", env={"MIOS_AI_KEY":"fixture-explicit-key"})
        self.assertEqual(values["exit"], 0)
        self.assertEqual(values["sdk_calls"], 1)
        self.assertEqual(values["endpoint"], "http://127.0.0.1:17002/v1")
        self.assertTrue(values["key_preserved"])

    def test_layer_fragments_user_precedence_and_empty_values(self):
        self.host.write_text('[ports.categories.agent]\nbase=18000\n[ai]\nagent_model="Host Public"\n')
        user_d = self.user.parent / "mios.d"
        user_d.mkdir()
        (user_d / "80-connection.toml").write_text('[ports.categories.agent]\nbase=19000\n[ai]\nagent_model="User Public"\n')
        (user_d / "90-empty.toml").write_text('[ai]\nendpoint=""\nagent_model=""\n')
        values, _ = self.run_cli()
        self.assertEqual(values["endpoint"], "http://127.0.0.1:19002/v1")
        self.assertEqual(values["model"], "User Public")

    def test_explicit_endpoint_model_key_and_gateway_compatibility(self):
        for endpoint in ("https://fixture.invalid:9443/openai/v1", "http://localhost:8642/v1"):
            values, result = self.run_cli(mode="main", env={"MIOS_AI_ENDPOINT":endpoint,
                "MIOS_AI_MODEL":"Explicit Model", "MIOS_AI_KEY":"fixture-explicit-key"})
            self.assertEqual(values["endpoint"], endpoint)
            self.assertEqual(values["model"], "Explicit Model")
            self.assertTrue(values["key_preserved"])
            self.assertNotIn("fixture-explicit-key", result.stdout + result.stderr)
        values, _ = self.run_cli(env={"MIOS_AI_MODEL":"base-override", "MIOS_AI_GATEWAY_MODEL":"Gateway Override"})
        self.assertEqual(values["model"], "Gateway Override")
        values, _ = self.run_cli(env={"MIOS_PORTS_AGENT_PIPE":"20001"})
        self.assertEqual(values["endpoint"], "http://127.0.0.1:20001/v1")
        values, _ = self.run_cli(env={"MIOS_AI_ENDPOINT":"http://fixture.invalid:${MIOS_PORTS_AGENT_PIPE}/v1"})
        self.assertEqual(values["endpoint"], "http://fixture.invalid:17002/v1")

    def test_native_shared_resolver_matches_python_expansion(self):
        import shutil
        if not shutil.which("mios-resolver"):
            self.skipTest("native resolver unavailable; Python shared resolution is exercised separately")
        native, _ = self.run_cli(mode="native", env={"MIOS_RESOLVER_NATIVE":"1"})
        if native.get("native_unavailable"):
            self.skipTest("native resolver has no layered baseline; shared fallback remains covered")
        fallback, _ = self.run_cli()
        self.assertEqual(native, fallback)

    def test_mios_toml_selector_uses_shared_override_contract(self):
        self.env.pop("MIOS_VENDOR_TOML")
        custom = self.root / "selected.toml"
        custom.write_text(self.vendor.read_text().replace("17000", "21000"))
        values, _ = self.run_cli(env={"MIOS_TOML":str(custom)})
        self.assertEqual(values["endpoint"], "http://127.0.0.1:21002/v1")

    def test_bad_connection_values_fail_before_sdk_without_secret_output(self):
        cases = (
            {"MIOS_AI_ENDPOINT":"http://localhost:${MIOS_MISSING_PRIVATE}/v1"},
            {"MIOS_AI_ENDPOINT":"http://localhost:not-a-port/v1"},
            {"MIOS_AI_ENDPOINT":"http://localhost:99999/v1"},
            {"MIOS_AI_ENDPOINT":"https://private-marker@fixture.invalid/v1"},
            {"MIOS_AI_ENDPOINT":"https://fixture.invalid/v1?key=private-marker"},
            {"MIOS_AI_MODEL":"${MIOS_MISSING_PRIVATE}"},
            {"MIOS_AI_KEY":"${MIOS_MISSING_PRIVATE}"},
            {"MIOS_AI_KEY":"private-marker\nheader"},
        )
        for env in cases:
            with self.subTest(fields=list(env)):
                values, result = self.run_cli(mode="main", env=env)
                self.assertEqual(values["exit"], 2)
                self.assertEqual(values["sdk_calls"], 0)
                self.assertIn("invalid AI configuration", result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertNotIn("private-marker", result.stdout + result.stderr)
                self.assertNotIn("MIOS_MISSING_PRIVATE", result.stdout + result.stderr)

    def test_malformed_toml_scalar_is_not_silently_stringified(self):
        for field in ("endpoint", "agent_model", "api_key"):
            original = self.vendor.read_text()
            import re
            self.vendor.write_text(re.sub(r'^'+field+r'=.*$', field+'=23', original, flags=re.M))
            values, result = self.run_cli(mode="main")
            self.assertEqual(values["exit"], 2)
            self.assertEqual(values["sdk_calls"], 0)
            self.assertIn("must be a string", result.stderr)
            self.vendor.write_text(original)

    def test_local_help_does_not_require_ai_configuration(self):
        values, _ = self.run_cli(mode="help", env={"MIOS_AI_ENDPOINT":"${MIOS_MISSING_PRIVATE}"})
        self.assertEqual(values["exit"], 0)
        self.assertEqual(values["sdk_calls"], 0)


if __name__ == "__main__":
    unittest.main()
