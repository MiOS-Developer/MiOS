#!/usr/bin/env python3
# AI-hint: Adversarial and regression test proving CLI verbs in usr/libexec/mios use subprocess with argv lists rather than shell string interpolation via os.system (TD-2).
# AI-doc: usr/share/doc/mios/manual/automation.md
"""Regression and safety tests for subprocess usage across usr/libexec/mios."""

import os
import re
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_fails = 0

def check(name, cond, detail=""):
    global _fails
    if not cond:
        _fails += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

def test_no_os_system_in_libexec():
    banned_needle = "os" + "." + "system("
    viol = []
    for dirpath, dirnames, filenames in os.walk(_HERE):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in ("__pycache__", "node_modules")]
        for fn in filenames:
            path = os.path.join(dirpath, fn)
            if not os.path.isfile(path) or fn.startswith("test_") or fn.endswith((".pyc", ".json", ".generated")):
                continue
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                    for idx, line in enumerate(fh, 1):
                        code = line.split("#")[0].strip()
                        if banned_needle in code:
                            rel = os.path.relpath(path, _HERE).replace(os.sep, "/")
                            viol.append(f"{rel}:{idx}: {line.strip()}")
            except OSError:
                continue
    check("libexec: zero os.system() invocations", len(viol) == 0, f"violations: {viol}")

def test_no_shell_true_in_libexec():
    banned_needle = "shell" + "=" + "True"
    viol = []
    for dirpath, dirnames, filenames in os.walk(_HERE):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in ("__pycache__", "node_modules")]
        for fn in filenames:
            path = os.path.join(dirpath, fn)
            if not os.path.isfile(path) or fn.startswith("test_") or fn.endswith((".pyc", ".json", ".generated")):
                continue
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                    for idx, line in enumerate(fh, 1):
                        code = line.split("#")[0].strip()
                        if banned_needle in code:
                            rel = os.path.relpath(path, _HERE).replace(os.sep, "/")
                            viol.append(f"{rel}:{idx}: {line.strip()}")
            except OSError:
                continue
    check("libexec: zero shell=True invocations", len(viol) == 0, f"violations: {viol}")

def test_mios_models_subprocess():
    banned_needle = "os" + "." + "system("
    path = os.path.join(_HERE, "mios-models")
    with open(path, "r", encoding="utf-8") as fh:
        content = fh.read()
    check("mios-models: imports subprocess", "import subprocess" in content)
    check("mios-models: does NOT invoke os.system", banned_needle not in content)
    check("mios-models: uses subprocess.run with argv list", "subprocess.run([sys.executable, str(fb)])" in content)

def test_mios_v2v_import_subprocess():
    banned_needle = "os" + "." + "system("
    path = os.path.join(_HERE, "mios-v2v-import")
    with open(path, "r", encoding="utf-8") as fh:
        content = fh.read()
    check("mios-v2v-import: imports subprocess", "import subprocess" in content)
    check("mios-v2v-import: does NOT invoke os.system", banned_needle not in content)
    check("mios-v2v-import: uses subprocess.run with cmd_list", "subprocess.run(cmd_list)" in content)

def test_mios_v2v_import_dry_run():
    path = os.path.join(_HERE, "mios-v2v-import")
    res = subprocess.run(["bash", path, "--dry-run"], capture_output=True, text=True)
    check("mios-v2v-import --dry-run exits 0", res.returncode == 0, f"rc={res.returncode}")
    check("mios-v2v-import --dry-run prints planned command", "Planned command: virt-v2v" in res.stdout)

def test_negative_control_detects_plant():
    banned_needle = "os" + "." + "system("
    sample_code = f'import os\n{banned_needle}"echo hacked")\n'
    detected = False
    for line in sample_code.splitlines():
        code = line.split("#")[0].strip()
        if re.search(r'\bos\.system\s*\(', code):
            detected = True
            break
    check("negative control: os.system regex matches planted string", detected)

def main():
    test_no_os_system_in_libexec()
    test_no_shell_true_in_libexec()
    test_mios_models_subprocess()
    test_mios_v2v_import_subprocess()
    test_mios_v2v_import_dry_run()
    test_negative_control_detects_plant()
    print(f"\n{'ok' if _fails == 0 else str(_fails) + ' FAILED'}")
    return 1 if _fails else 0

if __name__ == "__main__":
    sys.exit(main())
