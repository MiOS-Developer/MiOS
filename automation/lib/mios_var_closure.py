#!/usr/bin/env python3
# AI-hint: SSOT var-closure fitness function (drift-check 37). Proves R ⊆ E -- referenced MIOS_* variables are emitted by SSOT (task_2215).
# AI-doc: usr/share/doc/mios/manual/lib.md
"""MIOS_* consumer-closure gate: assert referenced ⊆ emitted."""
from __future__ import annotations
import glob
import importlib.util
import os
import re
import subprocess
import sys

try:
    import tomllib
except ImportError:
    import tomli as tomllib

ROOT = os.environ.get("MIOS_ROOT") or os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))


EMITTER_SUFFIXES = (
    "usr/lib/mios/userenv.sh", "tools/lib/userenv.sh",
    "usr/libexec/mios/system-sync-env.sh",
    "usr/share/mios/names.generated.txt",
    "usr/share/doc/mios/reference/naming-unification.md",
    "automation/lib/globals.sh", "automation/lib/globals.ps1",
    "tools/render-globals.py", "tools/render-ports.py",
    "usr/share/mios/mios.toml", "Justfile",
)
# A resolver that exists but cannot be imported yields a SHORTER emitted set, and
# every downstream verdict is computed against it. Record it; main() refuses to
# report on an emitted set it knows is partial.
EMIT_ERRORS: list[str] = []

VAR_RE = re.compile(r"MIOS_[A-Z0-9_]+")
DIRECTIVE_VARS = frozenset({
    "MIOS_APPLY_CLASS", "MIOS_SUBSTRATE", "MIOS_ROOT", "MIOS_VENDOR_TOML",
    "MIOS_HOST_TOML", "MIOS_USER_TOML", "MIOS_VENDOR_TOML_D", "MIOS_HOST_TOML_D",
    "MIOS_USER_TOML_D", "MIOS_CONFIG_DIR", "MIOS_TOML_ROOT", "MIOS_TOML",
})
CONSUMER_GLOBS = ("*.container", "*.service", "*.timer", "*.py", "*.sh", "*.toml",
                  "*.ps1", "*.psm1", "*.yaml", "*.yml", "Justfile", ".env.mios", "*.tmpl")


def _env_projection_names(data: dict) -> set[str]:
    """MIOS_* names a tracked .env projection supplies; a consumer that sources
    one gets them without the resolver cascade. Discovered from SSOT."""
    names = set()
    reg = (data.get("laws") or {}).get("projection_registry") or {}
    for surface in reg.get("surfaces") or []:
        if not isinstance(surface, dict):
            continue
        for out in str(surface.get("output", "")).split(","):
            out = out.strip()
            if not out.endswith(".env"):
                continue
            path = os.path.join(ROOT, out)
            if not os.path.isfile(path):
                continue
            with open(path, encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    m = re.match(r"\s*(?:export\s+)?(MIOS_[A-Z0-9_]+)\s*=", line)
                    if m:
                        names.add(m.group(1))
    return names


def emitted_set() -> set[str]:
    """Collect every exported MIOS_* name via Python SSOT resolver + mios.toml section prefixes."""
    emitted = set()
    EMIT_ERRORS.clear()          # idempotent across repeated calls in one process

    render_script = os.path.join(ROOT, "tools", "render-globals.py")
    if os.path.isfile(render_script):
        try:
            spec = importlib.util.spec_from_file_location("render_globals", render_script)
            rg = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(rg)
            emitted.update(rg.build_exports().keys())
        except Exception as exc:
            EMIT_ERRORS.append("tools/render-globals.py is present but unusable "
                               "(%s: %s)" % (type(exc).__name__, exc))

    toml_path = os.path.join(ROOT, "usr/share/mios/mios.toml")
    if os.path.isfile(toml_path):
        try:
            with open(toml_path, "rb") as fh:
                data = tomllib.load(fh)
            for k in data.keys():
                pref = "MIOS_" + k.upper().replace("-", "_").replace(".", "_") + "_"
                emitted.add(pref)
            emitted.update(_env_projection_names(data))
        except Exception as exc:
            EMIT_ERRORS.append("usr/share/mios/mios.toml is present but unusable "
                               "(%s: %s)" % (type(exc).__name__, exc))

    ue = os.path.join(ROOT, "usr/lib/mios/userenv.sh")
    if os.path.isfile(ue):
        try:
            env = {k: v for k, v in os.environ.items() if not k.startswith("MIOS_")}
            env.update(
                MIOS_VENDOR_TOML=toml_path,
                MIOS_HOST_TOML="/dev/null", MIOS_USER_TOML="/dev/null",
                MIOS_VENDOR_TOML_D="/nonexistent", MIOS_HOST_TOML_D="/nonexistent",
                MIOS_USER_TOML_D="/nonexistent"
            )
            out = subprocess.run(["bash", "-c", f". '{ue}'; env"], capture_output=True,
                                 text=True, env=env).stdout
            for line in out.splitlines():
                if line.startswith("MIOS_"):
                    m = VAR_RE.match(line.split("=", 1)[0])
                    if m:
                        emitted.add(m.group(0))
        except Exception:
            pass

    return emitted

def referenced_set(emitted: set[str] | None = None) -> dict[str, str]:
    """Every MIOS_* a non-emitter references that the resolver does not emit.

    EMITTER_SUFFIXES alone excludes the emitters; the path-prefix list that used
    to sit beside it excluded the CONSUMERS and took this set to 0 (task_989).
    """
    refs: dict[str, str] = {}
    known_emitted = emitted or set()
    table_prefixes = tuple(e for e in known_emitted if e.endswith("_"))

    for dirpath, dirs, files in os.walk(ROOT):
        norm_dir = dirpath.replace("\\", "/")
        # `.git` below ROOT marks a nested checkout: another repo's source.
        nested = dirpath != ROOT and ".git" in dirs + files
        if nested or any(sk in norm_dir for sk in ("/.git", "/.venv", "/node_modules", "/target", "/.claude", "/.agents", "/.gemini", "/.system_generated")):
            dirs[:] = []
            continue
        reldir = os.path.relpath(dirpath, ROOT).replace("\\", "/")
        if reldir.startswith("docs/") and "_design.md" in files:
            continue

        for fn in files:
            if fn.startswith("test_") or fn.endswith("_test.py") or "/tests/" in norm_dir or reldir == "tests" or reldir.startswith("tests/"):
                continue
            path = os.path.join(dirpath, fn)
            rel = os.path.relpath(path, ROOT).replace("\\", "/")
            if any(rel.endswith(s) for s in EMITTER_SUFFIXES):
                continue
            if not any(glob.fnmatch.fnmatchcase(fn, g) for g in CONSUMER_GLOBS):
                continue
            try:
                with open(path, encoding="utf-8", errors="ignore") as fh:
                    for n, line in enumerate(fh, 1):
                        code_part = line.split("#", 1)[0].split("//", 1)[0]
                        if not code_part.strip():
                            continue
                        for m in VAR_RE.finditer(code_part):
                            v = m.group(0)
                            if (v in DIRECTIVE_VARS or v.endswith("_")
                                    or v in known_emitted
                                    or any(v.startswith(p) for p in table_prefixes)):
                                continue
                            if re.search(rf"\b{v}\s*=", code_part):  # an assignment TO v is not a reference
                                continue
                            refs.setdefault(v, f"{rel}:{n}")
            except (OSError, UnicodeError):
                continue

    return refs

def main() -> int:
    E = emitted_set()
    R = referenced_set(E)
    if not E:
        print("mios-var-closure: FAIL -- emitter produced 0 vars (resolver broken?)", file=sys.stderr)
        return 2
    if EMIT_ERRORS:
        print("mios-var-closure: FAIL -- the emitted set is PARTIAL, so referenced-subset-of-emitted "
              "cannot be decided:", file=sys.stderr)
        for e in EMIT_ERRORS:
            print("  %s" % e, file=sys.stderr)
        return 2

    # R is already referenced-minus-emitted; re-filtering by E removed nothing.
    print(f"mios-var-closure: emitted={len(E)} referenced-but-unemitted={len(R)}")
    if R:
        print("FAIL -- referenced but NOT emitted (a consumer would lose its var):", file=sys.stderr)
        for v, loc in sorted(R.items()):                # no truncation: a ledger cannot be compared against a sample
            print(f"  {v}  ({loc})", file=sys.stderr)
        return 1

    print("PASS: all referenced MIOS_* variables are emitted by SSOT.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
