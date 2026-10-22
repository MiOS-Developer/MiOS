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

def run_cli(args, user_toml, models_dir=None):
    env = dict(os.environ)
    env["MIOS_USER_TOML"] = str(user_toml)
    env["MIOS_VENDOR_TOML"] = str(_VENDOR)
    env.pop("MIOS_HOST_TOML", None)
    r = subprocess.run([sys.executable, str(_CLI)] + args,
                       capture_output=True, text=True, env=env)
    return r.returncode, r.stdout + r.stderr

def mk_fetcher(tmp):
    """A copy of the fetcher with its absolute /var paths redirected into tmp,
    plus a curl stub that writes $MIOS_FAKE_PAYLOAD to curl's -o target."""
    models = tmp / "models"
    binn = tmp / "bin"
    models.mkdir(parents=True, exist_ok=True)
    binn.mkdir(parents=True, exist_ok=True)
    (binn / "curl").write_text(
        '#!/usr/bin/env bash\n'
        'out=""; while [ $# -gt 0 ]; do [ "$1" = "-o" ] && { out="$2"; shift; }; shift; done\n'
        'printf \'%s\' "$MIOS_FAKE_PAYLOAD" > "$out"\n')
    (binn / "curl").chmod(0o755)
    src = _FETCH.read_text(encoding="utf-8")
    src = (src.replace("/var/lib/mios/llamacpp/models", str(models))
              .replace("/var/lib/mios/.models-firstboot-done", str(tmp / "done"))
              .replace("/var/lib/mios/.models-firstboot-progress", str(tmp / "prog")))
    fetch = tmp / "fetch.py"
    fetch.write_text(src, encoding="utf-8")
    return fetch, models, binn

def run_fetcher(fetch, binn, toml_path, payload):
    env = dict(os.environ)
    env["PATH"] = f"{binn}{os.pathsep}{env['PATH']}"
    env["MIOS_TOML"] = str(toml_path)
    env["MIOS_FAKE_PAYLOAD"] = payload
    r = subprocess.run([sys.executable, str(fetch)],
                       capture_output=True, text=True, env=env)
    return r.stdout + r.stderr, r.returncode

def t_cli_reads_the_ssot():
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="miosmodels-"))
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
    payload = "the-real-weights"
    digest = hashlib.sha256(payload.encode()).hexdigest()

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="miosfetch-"))
    try:
        toml = tmp / "vendor.toml"
        toml.write_text(
            '[[ai.firstboot_models]]\n'
            'name = "good.gguf"\n'
            'source = "https://example.invalid/good.gguf"\n'
            f'sha256 = "{digest}"\n', encoding="utf-8")

        fetch, models, binn = mk_fetcher(tmp)
        out, rc = run_fetcher(fetch, binn, toml, payload)
        check("fetch: a matching payload verifies", "sha256 OK" in out, out)
        check("fetch: a matching payload is installed",
              (models / "good.gguf").exists(), out)
        check("fetch: a complete run writes the sentinel",
              (tmp / "done").exists(), out)
        check("fetch: a complete run exits 0", rc == 0, out)

        # Reset and replay with a substituted payload.
        (tmp / "done").unlink(missing_ok=True)
        (models / "good.gguf").unlink(missing_ok=True)
        out, rc = run_fetcher(fetch, binn, toml, "EVIL-substituted-weights")
        check("fetch: a mismatching payload is REJECTED",
              "MISMATCH" in out, out)
        check("fetch: the rejected weight is NOT installed",
              not (models / "good.gguf").exists(), out)
        check("fetch: the part file is discarded so a resume cannot poison it",
              not (models / "good.gguf.part").exists(), out)
        # The sentinel is the unit's ConditionPathExists gate. Writing it after a
        # rejected download would retire the provisioner having fetched nothing.
        check("fetch: a rejected model leaves the sentinel UNWRITTEN so the timer retries",
              not (tmp / "done").exists(), out)
        check("fetch: names what is still missing", "still missing" in out, out)
        check("fetch: still exits 0 (degrade-open; a pull never blocks boot)",
              rc == 0, out)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

def main():
    t_cli_reads_the_ssot()
    t_fetcher_verifies_sha256()
    print(f"\n{_fails} FAILED" if _fails else "\nok")
    return 1 if _fails else 0

if __name__ == "__main__":
    sys.exit(main())
