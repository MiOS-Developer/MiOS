#!/usr/bin/env python3
# AI-hint: Hermetic two-sided tests for mios-models-firstboot floating Hugging Face sources: with a mocked hub (fake curl on PATH, no network) a `resolve/main` entry with no SSOT sha256 is resolved to the current commit + upstream LFS sha256, fetched from that immutable commit, verified and recorded in models.tsv; a mismatching file and an entry with no provable digest are never installed.
# AI-related: usr/libexec/mios/mios-models-firstboot, usr/share/mios/mios.toml [ai.firstboot_models]
import hashlib
import json
import os
import pathlib
import shlex
import configparser
import time
import subprocess
import sys
import tempfile
import unittest
import http.server
import socket
import threading
import shutil
import tomllib

ROOT = pathlib.Path(os.environ.get("MIOS_TEST_ROOT") or pathlib.Path(__file__).resolve().parents[1])
FETCHER = ROOT / "usr/libexec/mios/mios-models-firstboot"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
REPO, FILE = "LiquidAI/LFM2-700M-GGUF", "LFM2-700M-Q4_K_M.gguf"

FAKE_CURL = r"""#!/usr/bin/env python3
import os, shutil, sys
args = sys.argv[1:]
out = args[args.index("-o") + 1] if "-o" in args else None
url = next(a for a in args if a.startswith(("http://", "https://")))
with open(os.environ["MOCK_LOG"], "a") as log:
    log.write(url + "\n")
if out and os.environ.get("MOCK_BARRIER"):
    import time
    barrier = os.environ["MOCK_BARRIER"]
    open(barrier + ".entered", "w").close()
    deadline = time.monotonic() + 10
    while not os.path.exists(barrier + ".release") and time.monotonic() < deadline:
        time.sleep(0.01)
path = os.path.join(os.environ["MOCK_HTTP"], url.split("://", 1)[1].replace("?", "__Q__"))
if not os.path.isfile(path):
    sys.exit(22)
if out:
    shutil.copyfile(path, out)
else:
    sys.stdout.write(open(path).read())
"""


class FirstbootLatest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = pathlib.Path(self.tmp.name)
        self.t = t
        (t / "bin").mkdir()
        curl = t / "bin/curl"
        curl.write_text(FAKE_CURL)
        curl.chmod(0o755)
        self.http = t / "http"
        self.weights = b"GGUF" + os.urandom(64)
        self._serve(self.weights, hashlib.sha256(self.weights).hexdigest())
        # Temp SSOT carrying this lane's fragment: the entry floats, no sha256 pin.
        self.toml = t / "mios.toml"
        self.toml.write_text(
            "[ai]\nfirstboot_pulls = true\n[[ai.firstboot_models]]\n"
            f'name = "lfm2-700m.gguf"\nsource = "https://huggingface.co/{REPO}/resolve/main/{FILE}"\n')
        self.models = t / "models"
        self.state = t / "state"
        self.log = t / "curl.log"

    def tearDown(self):
        self.tmp.cleanup()

    def _serve(self, payload, published_sha):
        api = self.http / f"huggingface.co/api/models/{REPO}/revision/main__Q__blobs=true"
        api.parent.mkdir(parents=True, exist_ok=True)
        api.write_text(json.dumps({"sha": COMMIT, "siblings": [
            {"rfilename": "README.md"},
            {"rfilename": FILE, "lfs": {"sha256": published_sha, "size": len(payload)}}]}))
        blob = self.http / f"huggingface.co/{REPO}/resolve/{COMMIT}/{FILE}"
        blob.parent.mkdir(parents=True, exist_ok=True)
        blob.write_bytes(payload)

    def _env(self, extra=None):
        env = {**os.environ, "PATH": f"{self.t / 'bin'}:{os.environ['PATH']}", "MOCK_HTTP": str(self.http),
               "MOCK_LOG": str(self.log), "MIOS_TOML": str(self.toml), "MIOS_MODELS_DIR": str(self.models),
               "MIOS_MODELS_STATE_DIR": str(self.state), "MIOS_TOML_GET": str(self.t / "absent"),
               "MIOS_HF_ENDPOINT": "https://huggingface.co",
               "MIOS_HOST_TOML": str(self.t / "host.toml"), "MIOS_USER_TOML": str(self.t / "user.toml"),
               "MIOS_VENDOR_TOML_D": str(self.t / "absent"), "MIOS_HOST_TOML_D": str(self.t / "absent"),
               "MIOS_USER_TOML_D": str(self.t / "absent")}
        env.update(extra or {})
        return env

    def _run(self):
        return subprocess.run([sys.executable, str(FETCHER)], env=self._env(), capture_output=True, text=True, timeout=120)

    def _run_with_selinux(self, *, deny=False):
        wrapper = self.t / "mios-model-label-probe.py"
        label_log = self.t / "label-probe.jsonl"
        wrapper.write_text(
            "import os,pathlib,runpy,json\n"
            "original=pathlib.Path.is_file\n"
            "pathlib.Path.is_file=lambda p: True if str(p)=='/sys/fs/selinux/enforce' else original(p)\n"
            "labels={}\n"
            "def get(fd,key):\n"
            " return labels.get(os.fstat(fd).st_ino,b'system_u:object_r:var_lib_t:s0')\n"
            "def put(fd,key,value):\n"
            " path=os.readlink('/proc/self/fd/'+str(fd))\n"
            f" with open({str(label_log)!r},'a') as log: log.write(json.dumps({{'path':path,'label':value.rstrip(bytes([0])).decode()}})+'\\n')\n"
            f" if {deny!r}: raise PermissionError('planted SELinux label denial')\n"
            " labels[os.fstat(fd).st_ino]=value\n"
            "os.getxattr=get;os.setxattr=put\n"
            f"runpy.run_path({str(FETCHER)!r},run_name='__main__')\n")
        result = subprocess.run([sys.executable, str(wrapper)], env=self._env(),
                                capture_output=True, text=True, timeout=120)
        records = [json.loads(line) for line in label_log.read_text().splitlines()] if label_log.exists() else []
        return result, records

    def test_shared_readonly_labels_apply_to_real_target_not_usr_link(self):
        served = self.t / "usr/share/mios/llamacpp/models"
        served.parent.mkdir(parents=True)
        self.models.mkdir()
        served.symlink_to(self.models, target_is_directory=True)
        with self.toml.open("a") as config:
            config.write(f'\n[llamacpp]\nmodels_dir="{served}"\nbake_models="lfm2-700m.gguf={REPO}:{FILE}"\n')
        result, labels = self._run_with_selinux()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertTrue((self.state / ".models-firstboot-done").exists(), result.stdout)
        self.assertTrue((served / ".ready").exists())
        paths = {row["path"] for row in labels}
        self.assertIn(str(self.models), paths)
        self.assertIn(str(self.models / ".ready"), paths)
        self.assertTrue(any(path.endswith("lfm2-700m.gguf.part") for path in paths))
        self.assertFalse(any(str(served) in path for path in paths))
        self.assertEqual({"system_u:object_r:container_ro_file_t:s0"}, {row["label"] for row in labels})

    def test_selinux_denial_invalidates_ready_and_completion_for_retry(self):
        with self.toml.open("a") as config:
            config.write(f'\n[llamacpp]\nbake_models="lfm2-700m.gguf={REPO}:{FILE}"\n')
        result = self._run()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertTrue((self.models / ".ready").exists())
        before = (self.models / "lfm2-700m.gguf").read_bytes()
        result, labels = self._run_with_selinux(deny=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("planted SELinux label denial", result.stdout)
        self.assertFalse((self.models / ".ready").exists())
        self.assertFalse((self.state / ".models-firstboot-done").exists())
        self.assertEqual(before, (self.models / "lfm2-700m.gguf").read_bytes())
        self.assertTrue(labels)

    def test_enabled_selinux_does_not_relabel_or_rewrite_baked_seed(self):
        seed = self.t / "usr/share/mios/llamacpp/models"
        seed.mkdir(parents=True)
        (seed / "lfm2-700m.gguf").write_bytes(self.weights)
        (seed / ".ready").touch()
        digest = hashlib.sha256(self.weights).hexdigest()
        (seed / "models.tsv").write_text("name\tsource\tcommit\tsha256\trecorded_at\n"
            f"lfm2-700m.gguf\thttps://huggingface.co/{REPO}/resolve/{COMMIT}/{FILE}\t{COMMIT}\t{digest}\tbuilt\n")
        before = {f.name:(f.read_bytes(),f.stat().st_mtime_ns,f.stat().st_mode) for f in seed.iterdir()}
        with self.toml.open("a") as config:
            config.write(f'\n[llamacpp]\nmodels_dir="{seed}"\nbake_models="lfm2-700m.gguf={REPO}:{FILE}"\n')
        result, labels = self._run_with_selinux(deny=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual([], labels)
        self.assertEqual(before, {f.name:(f.read_bytes(),f.stat().st_mtime_ns,f.stat().st_mode) for f in seed.iterdir()})
        self.assertTrue((self.state / ".models-firstboot-done").exists(), result.stdout)

    def test_cpu_node_uses_independently_configured_cpu_image(self):
        cpu_image = "registry.example.invalid/inference:cpu-fixture"
        gpu_image = "registry.example.invalid/inference:gpu-fixture"
        config = self.t / "cpu-image.toml"
        config.write_text(
            f'[image.sidecars]\nllm_light_cpu = "{cpu_image}"\n'
            f'llm_light = "{gpu_image}"\n')
        out = self.t / "quadlets"
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("MIOS_", "PYTHON", "CONTAINER_"))}
        env.update(
            MIOS_VENDOR_TOML=str(ROOT / "usr/share/mios/mios.toml"),
            MIOS_VENDOR_TOML_D=str(ROOT / "usr/lib/mios/mios.d"),
            MIOS_HOST_TOML=str(config), MIOS_USER_TOML="/dev/null",
            MIOS_HOST_TOML_D=str(self.t / "absent-host.d"),
            MIOS_USER_TOML_D=str(self.t / "absent-user.d"),
            MIOS_POD_OUT=str(out))
        result = subprocess.run(
            ["/usr/bin/mios-gen", "pod-quadlets", "--root", str(ROOT)],
            env=env, text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        node = (out / "mios-cpu-node.container").read_text()
        self.assertIn(f"Image={cpu_image}\n", node)
        self.assertNotIn(gpu_image, node)
        self.assertIn("--n-gpu-layers 0 ", node)
        with (ROOT / "usr/share/mios/mios.toml").open("rb") as source:
            ssot = tomllib.load(source)
        committed = (ROOT / "usr/share/containers/systemd/mios-cpu-node.container").read_text()
        self.assertIn(f'Image={ssot["image"]["sidecars"]["llm_light_cpu"]}\n', committed)

    def test_shared_model_and_static_config_mounts_do_not_relabel_usr(self):
        with (ROOT / "usr/share/mios/mios.toml").open("rb") as stream:
            config = tomllib.load(stream)
        for name in ("mios-cpu-node","mios-llm-light","mios-llm-worker@"):
            volumes = config["containers"][name]["Container"]["Volume"]
            model = [v for v in volumes if ":/models:" in v]
            self.assertEqual(1, len(model))
            self.assertTrue(model[0].endswith(":/models:ro"), model)
            self.assertNotIn(":Z", model[0])
            self.assertNotIn(":z", model[0])
        volumes = config["containers"]["mios-llm-light"]["Container"]["Volume"]
        self.assertIn("/usr/share/mios/llamacpp/mios-llm-light.yaml:/app/config.yaml:ro", volumes)
        self.assertTrue(any(v.endswith(":Z") and "/slots" in v for v in volumes))

    def test_real_curl_retries_interrupted_range_without_publishing_partial_readiness(self):
        payload = b"GGUF" + bytes(range(256)) * 64
        digest = hashlib.sha256(payload).hexdigest()
        state = {"attempts": 0, "ranges": []}

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_):
                pass

            def do_GET(self):
                if self.path.startswith("/api/models/"):
                    body = json.dumps({"sha": COMMIT, "siblings": [
                        {"rfilename": FILE, "lfs": {"sha256": digest}}
                    ]}).encode()
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                state["attempts"] += 1
                requested = self.headers.get("Range", "")
                state["ranges"].append(requested)
                start = int(requested.removeprefix("bytes=").removesuffix("-")) if requested else 0
                body = payload[start:]
                self.send_response(206 if requested else 200)
                if requested:
                    self.send_header("Content-Range", f"bytes {start}-{len(payload)-1}/{len(payload)}")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if state["attempts"] == 1:
                    self.wfile.write(body[:len(body)//2])
                    self.wfile.flush()
                    self.close_connection = True
                    self.connection.shutdown(socket.SHUT_RDWR)
                    return
                self.wfile.write(body)

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            real_curl = shutil.which("curl", path=os.defpath)
            self.assertIsNotNone(real_curl)
            (self.t / "bin/curl").unlink()
            (self.t / "bin/curl").symlink_to(real_curl)
            with self.toml.open("a") as config:
                config.write(f'\n[llamacpp]\nbake_models="lfm2-700m.gguf={REPO}:{FILE}"\n')
            for recover in (True, False):
                with self.subTest(recover=recover):
                    directory = self.t / ("positive" if recover else "negative")
                    models = directory / "models"
                    models.mkdir(parents=True)
                    (models / "lfm2-700m.gguf.part").write_bytes(payload[:1024])
                    fetcher = FETCHER
                    if not recover:
                        fetcher = directory / "usr/libexec/mios/mios-models-firstboot"
                        fetcher.parent.mkdir(parents=True)
                        library = directory / "usr/lib/mios"
                        library.parent.mkdir(parents=True)
                        library.symlink_to(ROOT / "usr/lib/mios", target_is_directory=True)
                        source = FETCHER.read_text()
                        self.assertEqual(source.count('"--retry-all-errors", '), 1)
                        fetcher.write_text(source.replace('"--retry-all-errors", ', "", 1))
                    state.update(attempts=0, ranges=[])
                    result = subprocess.run([sys.executable, str(fetcher)], env=self._env({
                        "MIOS_HF_ENDPOINT": f"http://127.0.0.1:{server.server_port}",
                        "MIOS_MODELS_DIR": str(models), "MIOS_MODELS_STATE_DIR": str(directory / "state"),
                    }), capture_output=True, text=True, timeout=20)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertEqual(state["ranges"][0], "bytes=1024-")
                    self.assertEqual((models / ".ready").exists(), recover)
                    self.assertEqual((directory / "state/.models-firstboot-done").exists(), recover)
                    if recover:
                        self.assertGreaterEqual(state["attempts"], 2)
                        self.assertEqual((models / "lfm2-700m.gguf").read_bytes(), payload)
                        self.assertFalse((models / "lfm2-700m.gguf.part").exists())
                    else:
                        self.assertEqual(state["attempts"], 1)
                        self.assertFalse((models / "lfm2-700m.gguf").exists())
                        self.assertTrue((models / "lfm2-700m.gguf.part").exists())
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)

    def test_restrictive_service_umask_still_publishes_readable_verified_models(self):
        with self.toml.open("a") as config:
            config.write(f'\n[llamacpp]\nbake_models="lfm2-700m.gguf={REPO}:{FILE}"\n')
        result = subprocess.run([sys.executable, str(FETCHER)], env=self._env(),
                                capture_output=True, text=True, timeout=120,
                                preexec_fn=lambda: os.umask(0o077))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        model = self.models / "lfm2-700m.gguf"
        self.assertEqual(model.read_bytes(), self.weights)
        self.assertEqual(model.stat().st_mode & 0o777, 0o640)
        self.assertEqual((model.stat().st_uid, model.stat().st_gid),
                         (self.models.stat().st_uid, self.models.stat().st_gid))
        self.assertEqual((self.models / "models.tsv").stat().st_mode & 0o777, 0o640)
        self.assertTrue((self.models / ".ready").exists())

    def test_existing_verified_model_permissions_are_repaired_without_blob_download(self):
        self.models.mkdir()
        model = self.models / "lfm2-700m.gguf"
        model.write_bytes(self.weights)
        model.chmod(0o600)
        ledger = self.models / "models.tsv"
        ledger.write_text("name\tsource\tcommit\tsha256\trecorded_at\n" +
                          f"lfm2-700m.gguf\thttps://huggingface.co/{REPO}/resolve/{COMMIT}/{FILE}\t" +
                          f"{COMMIT}\t{hashlib.sha256(self.weights).hexdigest()}\tbuilt\n")
        ledger.chmod(0o600)
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(model.read_bytes(), self.weights)
        self.assertEqual(model.stat().st_mode & 0o777, 0o640)
        self.assertEqual(ledger.stat().st_mode & 0o777, 0o640)
        self.assertEqual(len(ledger.read_text().splitlines()), 2)
        self.assertEqual(len(self.log.read_text().splitlines()), 1)
        self.assertTrue((self.state / ".models-firstboot-done").exists())

    def test_host_layer_disables_pulls_without_retiring_retries(self):
        (self.t / "host.toml").write_text("[ai]\nfirstboot_pulls=false\n")
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.log.exists())
        self.assertFalse((self.state / ".models-firstboot-done").exists())

    def test_required_light_fleet_is_verified_before_ready(self):
        with self.toml.open("a") as stream:
            stream.write(f'\n[llamacpp]\nbake_models="lfm2-700m.gguf={REPO}:{FILE}"\n')
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.models / ".ready").exists())
        self.assertEqual((self.models / "lfm2-700m.gguf").read_bytes(), self.weights)
        self.assertEqual(len(self.log.read_text().splitlines()), 2)  # metadata + blob, no duplicate fetch

    def test_existing_corrupt_model_is_replaced_only_with_verified_bytes(self):
        self.models.mkdir()
        (self.models / "lfm2-700m.gguf").write_bytes(b"broken")
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.models / "lfm2-700m.gguf").read_bytes(), self.weights)

    def test_invalid_entry_does_not_write_sentinel_or_escape_model_directory(self):
        self.toml.write_text('[ai]\n[[ai.firstboot_models]]\nname="../escaped"\nsource="https://example.invalid/weight"\n')
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.state / ".models-firstboot-done").exists())
        self.assertFalse((self.t / "escaped").exists())
        self.assertFalse(self.log.exists())

    def test_required_fleet_failure_keeps_retry_eligible_and_clears_stale_readiness(self):
        with self.toml.open("a") as stream:
            stream.write('[llamacpp]\nbake_models="missing.gguf=example/missing:missing.gguf"\n')
        self.models.mkdir()
        (self.models / ".ready").touch()
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.models / ".ready").exists())
        self.assertFalse((self.state / ".models-firstboot-done").exists())
        service = configparser.ConfigParser()
        service.read(ROOT / "usr/lib/systemd/system/mios-models-firstboot.service")
        self.assertFalse(service.getboolean("Service", "RemainAfterExit", fallback=False))
        timer = configparser.ConfigParser()
        timer.read(ROOT / "usr/lib/systemd/system/mios-models-firstboot.timer")
        self.assertEqual(timer["Timer"]["Unit"], "mios-models-firstboot.service")
        self.assertIn("OnUnitInactiveSec", timer["Timer"])

    def test_both_firstboot_services_become_inactive_for_timer_retries(self):
        for name in ("mios-models-firstboot", "mios-ai-firstboot"):
            service = configparser.ConfigParser()
            service.read(ROOT / f"usr/lib/systemd/system/{name}.service")
            timer = configparser.ConfigParser()
            timer.read(ROOT / f"usr/lib/systemd/system/{name}.timer")
            self.assertEqual(service["Service"]["Type"], "oneshot")
            self.assertFalse(service.getboolean("Service", "RemainAfterExit", fallback=False))
            self.assertEqual(timer["Timer"]["Unit"], f"{name}.service")
            self.assertIn("OnUnitInactiveSec", timer["Timer"])

    def test_finalizer_applies_system_and_user_presets_offline(self):
        script = (ROOT / "automation/88-finalize.sh").read_text()
        commands = [shlex.split(line) for line in script.splitlines()
                    if line.startswith("systemctl ") and "preset-all" in line]
        self.assertEqual(len(commands), 2)
        image = self.t / "image"
        paths = [
            "usr/lib/systemd/system/mios-models-firstboot.service",
            "usr/lib/systemd/system/mios-models-firstboot.timer",
            "usr/lib/systemd/system-preset/90-mios.preset",
            "usr/lib/systemd/user/mios-launcher.service",
            "usr/lib/systemd/user-preset/90-mios.preset",
        ]
        for path in paths:
            dest = image / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes((ROOT / path).read_bytes())
        for command in commands:
            # Run the exact finalizer commands against a disposable image root.
            result = subprocess.run([command[0], "--root", str(image), *command[1:]],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((image / "etc/systemd/system/timers.target.wants/mios-models-firstboot.timer").is_symlink())
        self.assertTrue((image / "etc/systemd/user/default.target.wants/mios-launcher.service").is_symlink())
        # Plant the original defect: system-only presets cannot enable a broker.
        broker = image / "etc/systemd/user/default.target.wants/mios-launcher.service"
        broker.unlink()
        system_only = next(command for command in commands if "--global" not in command)
        result = subprocess.run([system_only[0], "--root", str(image), *system_only[1:]],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(broker.exists())

    def test_heavy_weights_are_opt_in_and_use_a_repository_from_ssot(self):
        script = (ROOT / "usr/libexec/mios/mios-ai-firstboot").read_text()
        selection = script[script.index('_heavy_engine="${MIOS_AI_HEAVY_ENGINE:-}"'):script.index('VLLM_DIR=')]
        getter = self.t / "toml-get"
        getter.write_text("""#!/bin/sh
case "$1.$2" in
ai.heavy_engine) printf '%s' "${MIOS_TEST_HEAVY_ENGINE:-vllm}" ;;
ai.vllm.enable) printf '%s' "$MIOS_TEST_HEAVY_ENABLED" ;;
ai.sglang.bake_model) printf synthetic/weights ;;
*) exit 1 ;;
esac
""")
        getter.chmod(0o755)
        for engine, enabled, explicit, expected in [
                ("vllm", "false", "", ""), ("vllm", "true", "", "synthetic/weights"),
                ("vllm", "false", "operator/selected", "operator/selected"),
                ("llamacpp", "true", "", ""),
                ("llamacpp", "true", "operator/stale-hf-repository", "")]:
            result = subprocess.run(["bash", "-c", selection + '\nprintf "%s" "$VLLM_MODEL"'],
                env={**os.environ, "MIOS_TOML_GET": str(getter),
                     "MIOS_VLLM_BAKE_MODEL": explicit, "MIOS_TEST_HEAVY_ENABLED": enabled,
                     "MIOS_TEST_HEAVY_ENGINE": engine},
                capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, expected)

    def test_failed_reverification_invalidates_a_prior_completion_marker(self):
        self.assertEqual(self._run().returncode, 0)
        (self.models / "lfm2-700m.gguf").unlink()
        self._serve(b"corrupt", "0" * 64)
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.state / ".models-firstboot-done").exists())
        self.assertFalse((self.models / "lfm2-700m.gguf").exists())

    def test_malformed_types_degrade_open_without_readiness(self):
        for config in (
            '[ai]\n[[ai.firstboot_models]]\nname="x.gguf"\nsource=7\n',
            '[ai]\n[[ai.firstboot_models]]\nname={bad="value"}\nsource="https://invalid/x"\n',
            '[ai]\n[llamacpp]\nbake_models=["bad"]\n',
            '[ai]\nfirstboot_models={}\n',
            '[ai]\nfirstboot_pulls="false"\n',
        ):
            with self.subTest(config=config):
                self.toml.write_text(config)
                self.state.mkdir(exist_ok=True)
                self.models.mkdir(exist_ok=True)
                (self.state / ".models-firstboot-done").touch()
                (self.models / ".ready").touch()
                result = self._run()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertFalse((self.state / ".models-firstboot-done").exists())
                self.assertFalse((self.models / ".ready").exists())

    def test_concurrent_run_cannot_touch_the_active_download(self):
        barrier = self.t / "barrier"
        first = subprocess.Popen([sys.executable, str(FETCHER)],
            env=self._env({"MOCK_BARRIER": str(barrier)}), stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 5
            while not barrier.with_suffix(".entered").exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(barrier.with_suffix(".entered").exists())
            calls_before = self.log.read_text()
            second = self._run()
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertIn("already running", second.stdout)
            self.assertEqual(self.log.read_text(), calls_before)
            self.assertFalse((self.state / ".models-firstboot-done").exists())
            barrier.with_suffix(".release").touch()
            out, err = first.communicate(timeout=10)
            self.assertEqual(first.returncode, 0, out + err)
            self.assertEqual((self.models / "lfm2-700m.gguf").read_bytes(), self.weights)
            self.assertTrue((self.state / ".models-firstboot-done").exists())
        finally:
            barrier.with_suffix(".release").touch()
            if first.poll() is None:
                first.terminate()
                first.communicate(timeout=10)

    def test_baked_manifest_seed_needs_no_download_or_rewrite(self):
        seed = self.t / "seed"
        seed.mkdir()
        blob = seed / "lfm2-700m.gguf"
        blob.write_bytes(self.weights)
        (seed / ".ready").touch()
        digest = hashlib.sha256(self.weights).hexdigest()
        (seed / "models.tsv").write_text("name\tsource\tcommit\tsha256\trecorded_at\n"
                                       f"lfm2-700m.gguf\thttps://huggingface.co/{REPO}/resolve/{COMMIT}/{FILE}\t{COMMIT}\t{digest}\tbuilt\n")
        before = {f.name: (f.read_bytes(), f.stat().st_mtime_ns) for f in seed.iterdir()}
        with self.toml.open("a") as config:
            config.write(f'\n[llamacpp]\nmodels_dir="{seed}"\nbake_models="lfm2-700m.gguf={REPO}:{FILE}"\n')
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.log.exists())
        self.assertEqual(before, {f.name: (f.read_bytes(), f.stat().st_mtime_ns) for f in seed.iterdir()})
        self.assertTrue((self.state / ".models-firstboot-done").exists())
        self.assertFalse((self.models / "lfm2-700m.gguf").exists())

    def test_existing_bake_sbom_is_accepted_without_network(self):
        seed = self.t / "usr/share/mios/llamacpp/models"
        seed.mkdir(parents=True)
        (seed / "lfm2-700m.gguf").write_bytes(self.weights)
        (seed / ".ready").touch()
        digest = hashlib.sha256(self.weights).hexdigest()
        sbom = seed.parent.parent / "artifacts/sbom/models.tsv"
        sbom.parent.mkdir(parents=True)
        sbom.write_text(f"lfm2-700m.gguf\tgguf\t{REPO}\t{FILE}\t{digest}\n")
        with self.toml.open("a") as config:
            config.write(f'\n[llamacpp]\nmodels_dir="{seed}"\nbake_models="lfm2-700m.gguf={REPO}:{FILE}"\n')
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.log.exists())
        self.assertTrue((self.state / ".models-firstboot-done").exists())
        self.assertFalse((self.models / "lfm2-700m.gguf").exists())

    def test_baked_seed_cannot_override_a_changed_operator_source(self):
        seed = self.t / "seed"
        seed.mkdir()
        prior = b"GGUF-prior-source"
        (seed / "lfm2-700m.gguf").write_bytes(prior)
        (seed / ".ready").touch()
        digest = hashlib.sha256(prior).hexdigest()
        (seed / "models.tsv").write_text(
            f"lfm2-700m.gguf\thttps://huggingface.co/other/repo/resolve/{COMMIT}/{FILE}\t{COMMIT}\t{digest}\tbuilt\n")
        with self.toml.open("a") as config:
            config.write(f'\n[llamacpp]\nmodels_dir="{seed}"\nbake_models="lfm2-700m.gguf={REPO}:{FILE}"\n')
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.state / ".models-firstboot-done").exists())
        self.assertEqual((seed / "lfm2-700m.gguf").read_bytes(), prior)
        self.assertEqual(len(self.log.read_text().splitlines()), 1)  # metadata only, no seed write

    def test_missing_mutable_serving_path_links_to_persistent_models(self):
        served = self.t / "served"
        with self.toml.open("a") as config:
            config.write(f'\n[llamacpp]\nmodels_dir="{served}"\nbake_models="lfm2-700m.gguf={REPO}:{FILE}"\n')
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(served.is_symlink())
        self.assertEqual((served / "lfm2-700m.gguf").read_bytes(), self.weights)
        self.assertTrue((served / ".ready").exists())

    def test_unusable_serving_path_does_not_publish_readiness_elsewhere(self):
        served = self.t / "missing-parent" / "served"
        with self.toml.open("a") as config:
            config.write(f'\n[llamacpp]\nmodels_dir="{served}"\nbake_models="lfm2-700m.gguf={REPO}:{FILE}"\n')
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.log.exists())
        self.assertFalse((self.state / ".models-firstboot-done").exists())
        self.assertFalse((self.models / ".ready").exists())

    def test_floating_source_resolves_commit_and_upstream_digest(self):
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((self.models / "lfm2-700m.gguf").read_bytes(), self.weights)
        urls = self.log.read_text().split()
        self.assertIn(f"https://huggingface.co/{REPO}/resolve/{COMMIT}/{FILE}", urls, "not fetched from the immutable commit")
        rows = (self.models / "models.tsv").read_text().splitlines()
        self.assertEqual(rows[0].split("\t")[:4], ["name", "source", "commit", "sha256"])
        name, source, commit, digest, _ = rows[1].split("\t")
        self.assertEqual((name, commit, digest), ("lfm2-700m.gguf", COMMIT, hashlib.sha256(self.weights).hexdigest()))
        self.assertTrue((self.state / ".models-firstboot-done").exists())

    def test_mismatching_weight_is_never_installed(self):
        self._serve(b"poisoned", hashlib.sha256(self.weights).hexdigest())
        r = self._run()
        self.assertEqual(r.returncode, 0)  # degrade-open: never blocks boot
        self.assertIn("sha256 MISMATCH", r.stdout)
        self.assertFalse((self.models / "lfm2-700m.gguf").exists())
        self.assertFalse((self.models / "lfm2-700m.gguf.part").exists())
        self.assertFalse((self.models / "models.tsv").exists())
        self.assertFalse((self.state / ".models-firstboot-done").exists())

    def test_no_published_digest_means_no_download(self):
        api = self.http / f"huggingface.co/api/models/{REPO}/revision/main__Q__blobs=true"
        api.write_text(json.dumps({"sha": COMMIT, "siblings": [{"rfilename": FILE}]}))
        r = self._run()
        self.assertIn("upstream digest unavailable", r.stdout)
        self.assertNotIn(f"https://huggingface.co/{REPO}/resolve/{COMMIT}/{FILE}", self.log.read_text().split())
        self.assertFalse((self.models / "lfm2-700m.gguf").exists())

    def test_non_hf_source_without_digest_is_refused(self):
        self.toml.write_text('[[ai.firstboot_models]]\nname = "x.gguf"\nsource = "https://example.invalid/x.gguf"\n')
        r = self._run()
        self.assertIn("refusing an unverified weight", r.stdout)
        self.assertFalse((self.models / "x.gguf").exists())


if __name__ == "__main__":
    unittest.main()
