#!/usr/bin/env python3
# AI-hint: Two-sided controls for task_3433 -- both speech engines (piper, whisper) load a model baked into their bound image: no host model-store Volume, no [services] model_dir, no /var store in tmpfiles, no AssertPathExists on one.
# AI-related: usr/share/mios/mios.toml, usr/share/mios/piper/Containerfile, usr/share/containers/systemd/mios-whisper.container, usr/share/containers/systemd/mios-piper.container
# AI-functions: speech_engines, check_engine, TestSpeechStorage
"""task_3433: the speech engines bound a host model store nothing populated.
Read-only vendor models are image content (Law 12); nothing seeds /var, so a
host store plus AssertPathExists failed every stock boot.

For every speech engine (the [services.<engine>] tables named in IN_IMAGE) the
shipped tree must hold:

* [services.<engine>] carries no model_dir/model key (no host model store);
* usr/lib/tmpfiles.d declares nothing under /var/lib/mios/<engine>;
* the rendered Quadlet mounts no Volume whose source is under
  /var/lib/mios/<engine> or /srv (a host model store by another name), and no
  Volume whose target is /models or the engine's in-image model path (a bind
  there shadows the baked model), and asserts no path under
  /var/lib/mios/<engine>;
* its Exec= loads the model from the in-image path, and for an image MiOS
  builds, the Containerfile bakes that model at that path.

Each negative control plants one defect in a scratch copy and requires the
same check to name it.
"""
from __future__ import annotations

import glob
import os
import shlex
import shutil
import tempfile
import tomllib
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, "..", "..", ".."))
_TOML = os.path.join("usr", "share", "mios", "mios.toml")
_TMPFILES = os.path.join("usr", "lib", "tmpfiles.d")
_QUADLETS = os.path.join("usr", "share", "containers", "systemd")
_PIPER_CF = os.path.join("usr", "share", "mios", "piper", "Containerfile")

# Every speech engine; each must load an in-image model.
IN_IMAGE = ("piper", "whisper")

# Where each engine's image carries its model. A Volume targeting this path
# (or anything under it) replaces the baked model with host content.
MODEL_PATH = {"piper": "/usr/share/piper/voices", "whisper": "/app/models"}

# Host roots that are model stores whatever the in-container target is.
HOST_STORE_ROOTS = ("/srv",)


def speech_engines(root: str) -> dict[str, dict]:
    with open(os.path.join(root, _TOML), "rb") as f:
        services = tomllib.load(f).get("services", {})
    return {name: services.get(name, {}) for name in IN_IMAGE}


def _tmpfiles_paths(root: str) -> list[str]:
    paths: list[str] = []
    for conf in sorted(glob.glob(os.path.join(root, _TMPFILES, "*.conf"))):
        with open(conf, encoding="utf-8") as f:
            for line in f:
                fields = line.split()
                if len(fields) >= 2 and not fields[0].startswith("#"):
                    paths.append(fields[1].rstrip("/"))
    return paths


def _quadlet(root: str, engine: str) -> dict[str, dict[str, list[str]]]:
    sections: dict[str, dict[str, list[str]]] = {}
    cur: dict[str, list[str]] | None = None
    with open(os.path.join(root, _QUADLETS, f"mios-{engine}.container"), encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if s.startswith("[") and s.endswith("]"):
                cur = sections.setdefault(s[1:-1], {})
            elif cur is not None and "=" in s and not s.startswith("#"):
                k, v = s.split("=", 1)
                cur.setdefault(k, []).append(v)
    return sections


def _flag(argv: list[str], *names: str) -> str | None:
    for i, tok in enumerate(argv[:-1]):
        if tok in names:
            return argv[i + 1]
    return None


def _in_image_model(root: str, engine: str, spec: dict, argv: list[str]) -> list[str]:
    unit = f"mios-{engine}.container"
    if engine == "whisper":
        model = _flag(argv, "--model", "-m") or ""
        if not model.startswith("/app/models/"):
            return [f"{unit} Exec= does not load an in-image /app/models/ model (got {model or 'none'})"]
        return []
    # piper: -m names [services.piper].voice inside --data-dir, which the
    # Containerfile populates with download_voices --download-dir.
    errs: list[str] = []
    voice, data_dir = _flag(argv, "-m", "--model"), _flag(argv, "--data-dir")
    if not spec.get("voice"):
        errs.append("[services.piper] names no baked voice")
    # The generator projects ${MIOS_SERVICES_PIPER_VOICE} to the SSOT voice.
    if not spec.get("voice") or voice not in (spec["voice"], "${MIOS_SERVICES_PIPER_VOICE}"):
        errs.append(f"{unit} Exec= -m is {voice}, not the baked voice {spec.get('voice')}")
    if not data_dir or data_dir.startswith(("/models", "/var")):
        errs.append(f"{unit} Exec= --data-dir {data_dir} is not an in-image path")
        return errs
    with open(os.path.join(root, _PIPER_CF), encoding="utf-8") as f:
        cf = f.read()
    if f"--download-dir {data_dir}" not in cf or f'test -s "{data_dir}/${{MIOS_SERVICES_PIPER_VOICE}}.onnx"' not in cf:
        errs.append(f"{_PIPER_CF} does not bake ${{MIOS_SERVICES_PIPER_VOICE}}.onnx into {data_dir}")
    return errs


def _under(path: str, root: str) -> bool:
    path, root = os.path.normpath(path), os.path.normpath(root)
    return path == root or path.startswith(root.rstrip("/") + "/")


def _mounts(q: dict) -> list[tuple[str, str]]:
    """(source, target) for every Volume=, Mount= and Tmpfs= of the unit; a tmpfs has no source."""
    out = []
    for v in q.get("Container", {}).get("Volume", []):
        parts = v.split(":")
        out.append((parts[0], parts[1] if len(parts) >= 2 else parts[0]))
    for m in q.get("Container", {}).get("Mount", []):
        kv = dict(f.split("=", 1) for f in m.split(",") if "=" in f)
        out.append((kv.get("source", kv.get("src", "")),
                    kv.get("target", kv.get("destination", kv.get("dst", "")))))
    for t in q.get("Container", {}).get("Tmpfs", []):
        out.append(("", t.split(":")[0]))
    return out


def _model_targets(engine: str, argv: list[str]) -> list[str]:
    targets = ["/models", MODEL_PATH[engine]]
    if engine == "piper":
        data_dir = _flag(argv, "--data-dir")
        if data_dir:
            targets.append(data_dir)
    else:
        model = _flag(argv, "--model", "-m")
        if model and "/" in model:
            targets.append(os.path.dirname(model))
    return sorted({t.rstrip("/") for t in targets if t})


def check_engine(root: str, engine: str, spec: dict) -> list[str]:
    errors: list[str] = []
    unit = f"mios-{engine}.container"
    store = f"/var/lib/mios/{engine}"
    for key in ("model_dir", "model"):
        if key in spec:
            errors.append(f"[services.{engine}].{key} declares a host model store")
    for p in _tmpfiles_paths(root):
        if p == store or p.startswith(store + "/"):
            errors.append(f"tmpfiles.d declares {p}, a host model store for {engine}")
    q = _quadlet(root, engine)
    execs = q.get("Container", {}).get("Exec", [])
    argv = shlex.split(" ".join(execs).replace("'", "")) if execs else []
    targets = _model_targets(engine, argv)
    for src, dst in _mounts(q):
        if src.startswith("/") and (_under(src, store) or any(_under(src, h) for h in HOST_STORE_ROOTS)):
            errors.append(f"{unit} mounts host model store {src} (at {dst})")
        # At, below or above the model path: a mount over an ancestor hides it too.
        for t in targets:
            if dst and (_under(dst, t) or _under(t, dst)):
                errors.append(f"{unit} mounts {src or 'a tmpfs'} over in-image model path {dst}")
                break
    for a in q.get("Unit", {}).get("AssertPathExists", []):
        if a.startswith(store):
            errors.append(f"{unit} asserts host model path {a}")
    if not argv:
        errors.append(f"{unit} has no Exec=")
    else:
        errors.extend(_in_image_model(root, engine, spec, argv))
    return errors


class TestSpeechStorage(unittest.TestCase):
    def setUp(self) -> None:
        self.engines = speech_engines(_ROOT)
        self.scratch = tempfile.mkdtemp(prefix="t1140_")
        for rel in (_TMPFILES, _QUADLETS):
            shutil.copytree(os.path.join(_ROOT, rel), os.path.join(self.scratch, rel))
        for rel in (_TOML, _PIPER_CF):
            os.makedirs(os.path.join(self.scratch, os.path.dirname(rel)), exist_ok=True)
            shutil.copy2(os.path.join(_ROOT, rel), os.path.join(self.scratch, rel))

    def tearDown(self) -> None:
        shutil.rmtree(self.scratch, ignore_errors=True)

    def _plant(self, rel: str, old: str, new: str) -> None:
        path = os.path.join(self.scratch, rel)
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn(old, text, f"plant anchor missing from {rel}")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text.replace(old, new, 1))

    def _append(self, rel: str, text: str) -> None:
        with open(os.path.join(self.scratch, rel), "a", encoding="utf-8") as f:
            f.write(text)

    def test_negative_mount_shapes_that_hide_the_model(self) -> None:
        """Non-normalized store sources, ancestor targets, Mount= binds and a Tmpfs= over the model."""
        plants = {
            "piper": ["Volume=/var/lib//mios/piper/models:/data:ro,Z",
                      "Volume=/opt/x:/usr/share/piper:ro,Z",
                      "Mount=type=bind,source=/var/lib/mios/piper,target=/usr/share/piper/voices",
                      "Tmpfs=/usr/share/piper/voices"],
            "whisper": ["Volume=/var/lib/mios/./whisper:/data:ro,Z",
                        "Volume=/opt/x:/app:ro,Z",
                        "Mount=type=bind,src=/srv/w,dst=/app/models",
                        "Tmpfs=/app"],
        }
        for engine, lines in plants.items():
            for line in lines:
                with self.subTest(engine=engine, plant=line):
                    rel = os.path.join(_QUADLETS, f"mios-{engine}.container")
                    path = os.path.join(self.scratch, rel)
                    with open(path, encoding="utf-8") as f:
                        orig = f.read()
                    self._plant(rel, "Volume=/run/mios:/run/mios:Z", f"{line}\nVolume=/run/mios:/run/mios:Z")
                    errs = check_engine(self.scratch, engine, self.engines[engine])
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(orig)
                    self.assertTrue(errs, f"{line} passed check_engine for {engine}")

    def test_both_engines_are_checked(self) -> None:
        # Guards against a vacuous pass: both units exist and both are walked.
        for engine in IN_IMAGE:
            self.assertTrue(os.path.isfile(os.path.join(_ROOT, _QUADLETS, f"mios-{engine}.container")), engine)
            self.assertIn("Exec", _quadlet(_ROOT, engine).get("Container", {}), engine)

    def test_positive_shipped_tree(self) -> None:
        for engine, spec in self.engines.items():
            self.assertEqual(check_engine(_ROOT, engine, spec), [], engine)

    def test_negative_models_bind_planted(self) -> None:
        """The pre-fix shape: a host store bound at /models."""
        for engine in IN_IMAGE:
            rel = os.path.join(_QUADLETS, f"mios-{engine}.container")
            self._plant(rel, "Volume=/run/mios:/run/mios:Z",
                        f"Volume=/var/lib/mios/{engine}/models:/models:ro,Z\nVolume=/run/mios:/run/mios:Z")
            errs = check_engine(self.scratch, engine, self.engines[engine])
            self.assertIn(f"mios-{engine}.container mounts host model store /var/lib/mios/{engine}/models (at /models)", errs)
            self.assertIn(f"mios-{engine}.container mounts /var/lib/mios/{engine}/models over in-image model path /models", errs)

    def test_negative_var_store_at_other_target_planted(self) -> None:
        """A host /var/lib/mios/<engine> source is a model store wherever it lands."""
        for engine in IN_IMAGE:
            rel = os.path.join(_QUADLETS, f"mios-{engine}.container")
            self._plant(rel, "Volume=/run/mios:/run/mios:Z",
                        f"Volume=/var/lib/mios/{engine}:/data:ro,Z\nVolume=/run/mios:/run/mios:Z")
            errs = check_engine(self.scratch, engine, self.engines[engine])
            self.assertIn(f"mios-{engine}.container mounts host model store /var/lib/mios/{engine} (at /data)", errs)

    def test_negative_srv_source_planted(self) -> None:
        for engine in IN_IMAGE:
            rel = os.path.join(_QUADLETS, f"mios-{engine}.container")
            self._plant(rel, "Volume=/run/mios:/run/mios:Z",
                        f"Volume=/srv/mios/{engine}:/data:ro,Z\nVolume=/run/mios:/run/mios:Z")
            errs = check_engine(self.scratch, engine, self.engines[engine])
            self.assertIn(f"mios-{engine}.container mounts host model store /srv/mios/{engine} (at /data)", errs)

    def test_negative_var_store_over_model_path_planted(self) -> None:
        """The evasion the /models-only check let through: the host store bound
        straight over the path the image carries its model at."""
        for engine in IN_IMAGE:
            rel = os.path.join(_QUADLETS, f"mios-{engine}.container")
            path = MODEL_PATH[engine]
            self._plant(rel, "Volume=/run/mios:/run/mios:Z",
                        f"Volume=/var/lib/mios/{engine}/models:{path}:ro,Z\nVolume=/run/mios:/run/mios:Z")
            errs = check_engine(self.scratch, engine, self.engines[engine])
            self.assertIn(f"mios-{engine}.container mounts host model store /var/lib/mios/{engine}/models (at {path})", errs)
            self.assertIn(f"mios-{engine}.container mounts /var/lib/mios/{engine}/models over in-image model path {path}", errs)

    def test_negative_any_source_over_model_path_planted(self) -> None:
        """Any Volume (named volume or another host dir) over the in-image model
        path shadows the baked model."""
        for engine in IN_IMAGE:
            rel = os.path.join(_QUADLETS, f"mios-{engine}.container")
            path = MODEL_PATH[engine]
            self._plant(rel, "Volume=/run/mios:/run/mios:Z",
                        f"Volume=mios-{engine}-models:{path}/sub:Z\nVolume=/run/mios:/run/mios:Z")
            errs = check_engine(self.scratch, engine, self.engines[engine])
            self.assertIn(f"mios-{engine}.container mounts mios-{engine}-models over in-image model path {path}/sub", errs)

    def test_negative_var_store_planted(self) -> None:
        for engine in IN_IMAGE:
            self._append(os.path.join(_TMPFILES, "mios-t1140-plant.conf"),
                         f"d /var/lib/mios/{engine}/models 0755 root root -\n")
            spec = dict(self.engines[engine], model_dir=f"/var/lib/mios/{engine}/models")
            errs = check_engine(self.scratch, engine, spec)
            self.assertIn(f"tmpfiles.d declares /var/lib/mios/{engine}/models, a host model store for {engine}", errs)
            self.assertIn(f"[services.{engine}].model_dir declares a host model store", errs)

    def test_negative_pre_fix_piper_exec(self) -> None:
        """Pre-fix piper: --model /models/<file> with an asserted /var store."""
        rel = os.path.join(_QUADLETS, "mios-piper.container")
        self._plant(rel, "Exec=-m en_US-lessac-medium --data-dir /usr/share/piper/voices",
                    "Exec=--model /models/en_US-lessac-medium.onnx")
        self._plant(rel, "[Unit]\n",
                    "[Unit]\nAssertPathExists=/var/lib/mios/piper/models/en_US-lessac-medium.onnx\n")
        errs = check_engine(self.scratch, "piper", self.engines["piper"])
        self.assertIn("mios-piper.container asserts host model path "
                      "/var/lib/mios/piper/models/en_US-lessac-medium.onnx", errs)
        self.assertIn("mios-piper.container Exec= -m is /models/en_US-lessac-medium.onnx, "
                      "not the baked voice en_US-lessac-medium", errs)

    def test_negative_voice_not_baked(self) -> None:
        self._plant(_PIPER_CF, "--download-dir /usr/share/piper/voices", "--download-dir /tmp/voices")
        self.assertIn(f"{_PIPER_CF} does not bake ${{MIOS_SERVICES_PIPER_VOICE}}.onnx into /usr/share/piper/voices",
                      check_engine(self.scratch, "piper", self.engines["piper"]))

    def test_negative_whisper_host_model(self) -> None:
        self._plant(os.path.join(_QUADLETS, "mios-whisper.container"),
                    "--model /app/models/", "--model /models/")
        self.assertIn("mios-whisper.container Exec= does not load an in-image /app/models/ model "
                      "(got /models/ggml-base.en.bin)",
                      check_engine(self.scratch, "whisper", self.engines["whisper"]))


if __name__ == "__main__":
    unittest.main()
