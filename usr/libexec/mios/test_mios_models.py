#!/usr/bin/env python3
# AI-hint: Stdlib offline tests for the FBM model plane (task_181).
# AI-doc: usr/share/doc/mios/manual/mios.md

import hashlib
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

_HERE = pathlib.Path(__file__).resolve().parent
_CLI = _HERE / "mios-models"
_FETCH = _HERE / "mios-models-firstboot"
_ROOT = _HERE.parents[2]          # usr/libexec/mios -> repo root
_VENDOR = _ROOT / "usr" / "share" / "mios" / "mios.toml"

_fails = 0

def check(name, cond, detail=""):
    global _fails
    if cond:
        print(f"ok   - {name}")
    else:
        _fails += 1
        print(f"FAIL - {name}" + (f" -- {detail}" if detail else ""))

def isolated_env(tmp, vendor, user):
    """Resolve the checkout's code against only this fixture's TOML layers."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("MIOS_")}
    env.update({
        "PYTHONPATH": str(_ROOT / "usr/lib/mios"),
        "MIOS_VENDOR_TOML": str(vendor),
        "MIOS_HOST_TOML": str(tmp / "absent-host.toml"),
        "MIOS_USER_TOML": str(user),
        "MIOS_VENDOR_TOML_D": str(tmp / "absent-vendor.d"),
        "MIOS_HOST_TOML_D": str(tmp / "absent-host.d"),
        "MIOS_USER_TOML_D": str(tmp / "absent-user.d"),
    })
    return env


def run_cli(args, user_toml):
    env = isolated_env(user_toml.parent, _VENDOR, user_toml)
    # Load the actual CLI and redirect its disk view before entering main.
    # In particular, rm must never inspect or remove a deployed model.
    driver = (
        "import importlib.machinery,importlib.util,pathlib,sys;"
        "loader=importlib.machinery.SourceFileLoader('mios_models_test',sys.argv[1]);"
        "spec=importlib.util.spec_from_loader(loader.name,loader);"
        "cli=importlib.util.module_from_spec(spec);loader.exec_module(cli);"
        "cli.MODELS_DIR=pathlib.Path(sys.argv.pop(2));sys.argv.pop(1);cli.main()"
    )
    r = subprocess.run(
        [sys.executable, "-c", driver, str(_CLI), str(user_toml.parent / "models"), *args],
        capture_output=True, text=True, env=env, timeout=30)
    return r.returncode, r.stdout + r.stderr


def mk_fetcher(tmp):
    """Keep the real provisioner unchanged; replace only offline transport."""
    models = tmp / "models"
    binn = tmp / "bin"
    models.mkdir(parents=True, exist_ok=True)
    binn.mkdir(parents=True, exist_ok=True)
    (binn / "curl").write_text(
        "#!/usr/bin/env python3\n"
        "import os,pathlib,shutil,sys\n"
        "args=sys.argv[1:]\n"
        "out=args[args.index('-o')+1]\n"
        "payload=pathlib.Path(os.environ['MIOS_TEST_PAYLOAD'])\n"
        "if payload.is_file(): shutil.copyfile(payload,out)\n")
    (binn / "curl").chmod(0o755)
    return models, binn


def run_fetcher(tmp, binn, toml_path, payload):
    env = isolated_env(tmp, toml_path, tmp / "absent-user.toml")
    env["PATH"] = f"{binn}{os.pathsep}{env['PATH']}"
    env["MIOS_MODELS_DIR"] = str(tmp / "models")
    env["MIOS_MODELS_STATE_DIR"] = str(tmp / "state")
    payload_path = tmp / "payload"
    if payload is not None:
        payload_path.write_bytes(payload)
    else:
        payload_path.unlink(missing_ok=True)
    env["MIOS_TEST_PAYLOAD"] = str(payload_path)
    r = subprocess.run([sys.executable, str(_FETCH)],
                       capture_output=True, text=True, env=env, timeout=30)
    return r.stdout + r.stderr, r.returncode

def t_cli_reads_the_ssot():
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="mios-model-cli-"))
    try:
        user = tmp / "mios.toml"
        vendor_before = hashlib.sha256(_VENDOR.read_bytes()).hexdigest()
        rc, out = run_cli(["list"], user)
        check("list: empty declaration set is reported, not an error", rc == 0)
        check("list: says the set is empty", "Declared in" in out, out)

        rc, out = run_cli(["add", "a.gguf", "https://example.invalid/a"], user)
        check("add: succeeds", rc == 0, out)
        check("add: warns when no sha256 is given",
              "provisioned unverified" in out, out)
        check("add: wrote the USER overlay", user.exists())
        check("add: did NOT touch the vendor file",
              hashlib.sha256(_VENDOR.read_bytes()).hexdigest() == vendor_before,
              "the vendor mios.toml changed")

        rc, out = run_cli(["add", "b.gguf", "https://example.invalid/b",
                           "deadbeef", "llm_light"], user)
        check("add: accepts sha256 + lane", rc == 0, out)
        body = user.read_text(encoding="utf-8")
        check("add: records the digest", 'sha256 = "deadbeef"' in body, body)
        check("add: records the lane", 'lane   = "llm_light"' in body, body)

        rc, out = run_cli(["list"], user)
        check("list: reads the LAYERED overlay, not just the vendor file",
              "a.gguf" in out and "b.gguf" in out, out)
        check("list: a declared model absent from disk shows as MISSING",
              "[MISSING]" in out, out)

        rc, out = run_cli(["add", "a.gguf", "https://example.invalid/other"], user)
        check("add: a duplicate name is refused", rc == 1, out)

        rc, out = run_cli(["rm", "a.gguf"], user)
        check("rm: removes the declaration", rc == 0, out)
        body = user.read_text(encoding="utf-8")
        check("rm: dropped only the named entry",
              "a.gguf" not in body and "b.gguf" in body, body)

        rc, out = run_cli(["rm", "nope.gguf"], user)
        check("rm: an unknown name is a non-zero no-op", rc == 1, out)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

def t_fetcher_verifies_sha256():
    payload = b"GGUF\x00the-real-weights\xff"
    digest = hashlib.sha256(payload).hexdigest()
    source = "https://example.invalid/good.gguf"

    # These controls exercise the real verification and publication path.
    # Human-facing log phrasing is not an integrity or retry contract.
    cases = (
        ("matching", payload, digest, True),
        ("substituted", b"EVIL-substituted-weights", digest, False),
        ("empty", b"", digest, False),
        ("missing", None, digest, False),
        ("invalid digest", payload, "deadbeef", False),
        ("unprovable digest", payload, "", False),
    )
    for label, downloaded, expected_digest, accepted in cases:
        with tempfile.TemporaryDirectory(prefix="mios-model-fetch-") as directory:
            tmp = pathlib.Path(directory)
            models, binn = mk_fetcher(tmp)
            state = tmp / "state"
            state.mkdir()
            done = state / ".models-firstboot-done"
            ready = models / ".ready"
            # A failed re-verification must invalidate old completion gates;
            # the success case must publish fresh markers of its own.
            if not accepted:
                done.touch()
                ready.touch()
            toml = tmp / "vendor.toml"
            toml.write_text(
                '[ai]\nfirstboot_pulls = true\n'
                '[[ai.firstboot_models]]\n'
                'name = "good.gguf"\n'
                f'source = "{source}"\n'
                f'sha256 = "{expected_digest}"\n'
                '[llamacpp]\n'
                f'models_dir = "{models.as_posix()}"\n'
                'bake_models = "good.gguf=offline/test:good.gguf"\n',
                encoding="utf-8")
            out, rc = run_fetcher(tmp, binn, toml, downloaded)
            target = models / "good.gguf"
            ledger = models / "models.tsv"
            check(f"fetch {label}: exits 0 without blocking boot", rc == 0, out)
            check(f"fetch {label}: no unverified partial remains",
                  not (models / "good.gguf.part").exists(), out)
            if accepted:
                actual = target.read_bytes() if target.is_file() else None
                check("fetch: installs the exact matching bytes", actual == payload, out)
                check("fetch: installed bytes match the declared sha256",
                      actual is not None and hashlib.sha256(actual).hexdigest() == digest, out)
                rows = ([line.split("\t") for line in ledger.read_text().splitlines()[1:]]
                        if ledger.exists() else [])
                check("fetch: provenance records the verified name, source and digest",
                      any(row[:4] == ["good.gguf", source, "", digest] for row in rows), out)
                check("fetch: the verified required fleet publishes readiness", ready.is_file(), out)
                check("fetch: a complete run writes the sentinel", done.is_file(), out)
            else:
                check(f"fetch {label}: rejected weight is not installed", not target.exists(), out)
                check(f"fetch {label}: no successful provenance is recorded", not ledger.exists(), out)
                check(f"fetch {label}: stale completion sentinel is invalidated", not done.exists(), out)
                check(f"fetch {label}: stale serving readiness is invalidated", not ready.exists(), out)
                progress = state / ".models-firstboot-progress"
                check(f"fetch {label}: retry state identifies the incomplete model",
                      progress.is_file() and "good.gguf" in progress.read_text(), out)

def main():
    t_cli_reads_the_ssot()
    t_fetcher_verifies_sha256()
    print(f"\n{_fails} FAILED" if _fails else "\nok")
    return 1 if _fails else 0

if __name__ == "__main__":
    sys.exit(main())
