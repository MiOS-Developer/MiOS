#!/usr/bin/env python3
# AI-hint: SSOT-plane drift gates in one module: mios.toml integrity, consumer keys, unit projection, port fallbacks and binding, variant registry, deploy formats, role SSOT, node pool, blade coverage and fleet safety. The subcommand selects the gate.
# AI-doc: usr/share/doc/mios/manual/tools.md
"""SSOT-plane drift gates. One module, one subcommand per gate."""
import sys


"""Gate: mios.toml parses as valid TOML, maintains min line count, and preserves top-level tables."""

import os
import sys

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None

mti_MIOS_TOML_RELATIVE = "usr/share/mios/mios.toml"
mti_MIN_LINE_COUNT = 9000

# Required top-level tables that must always be present in mios.toml
mti_REQUIRED_TOP_LEVEL_TABLES = {
    "versions",
    "security",
    "units",
    "unit_projection",
    "docs",
    "legibility",
    "ports",
}

def mti_main() -> int:
    root = os.environ.get("MIOS_DRIFT_ROOT", os.environ.get("MIOS_TOML_ROOT", "."))
    path = os.path.join(root, mti_MIOS_TOML_RELATIVE)
    if not os.path.isfile(path):
        print(f"VIOLATION: {mti_MIOS_TOML_RELATIVE} not found under {root}")
        return 1

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    lines = content.splitlines()
    line_count = len(lines)
    if line_count < mti_MIN_LINE_COUNT:
        print(
            f"VIOLATION: {mti_MIOS_TOML_RELATIVE} line count ({line_count}) is below minimum baseline ({mti_MIN_LINE_COUNT})"
        )
        return 1

    if tomllib is not None:
        try:
            parsed = tomllib.loads(content)
        except Exception as e:
            print(f"VIOLATION: {mti_MIOS_TOML_RELATIVE} failed TOML parsing: {e}")
            return 1

        missing_tables = [table for table in mti_REQUIRED_TOP_LEVEL_TABLES if table not in parsed]
        if missing_tables:
            print(
                f"VIOLATION: {mti_MIOS_TOML_RELATIVE} is missing required top-level tables: {missing_tables}"
            )
            return 1
    else:
        # Fallback basic header check if tomllib/tomli unavailable
        found_tables = set()
        for line in lines:
            line_str = line.strip()
            if line_str.startswith("[") and not line_str.startswith("[["):
                header = line_str.strip("[]").strip().split(".")[0]
                found_tables.add(header)
        missing_tables = [table for table in mti_REQUIRED_TOP_LEVEL_TABLES if table not in found_tables]
        if missing_tables:
            print(
                f"VIOLATION: {mti_MIOS_TOML_RELATIVE} is missing required top-level tables: {missing_tables}"
            )
            return 1

    print(f"mios.toml integrity check passed (lines={line_count})")
    return 0


"""Gate: every SSOT key a consumer reads is a key the SSOT declares."""

import os
import re
import sys

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover -- py<3.11
    import tomli as tomllib  # type: ignore

sck_TOML = "usr/share/mios/mios.toml"
sck_SCAN_ROOTS = ("usr",)

# `_toml_section("x").get("y"` and `(_toml_section("x") or {}).get("y"`.
sck__READ = re.compile(
    r"""_toml_section\(\s*["']([a-z0-9_.]+)["']\s*\)(?:\s*or\s*\{\}\s*\))?"""
    r"""\s*\.get\(\s*["']([a-z0-9_]+)["']"""
)

def sck_consumer_reads(root: str) -> dict:
    """{(table, key): [file:line, ...]} over shipped Python.

    Tests are skipped: a test may legitimately read a key it stubs itself.
    """
    hits = {}
    for scan in sck_SCAN_ROOTS:
        base = os.path.join(root, scan)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames
                           if d != "__pycache__" and not d.startswith(".venv")]
            for name in sorted(filenames):
                if not name.endswith(".py") or name.startswith("test_"):
                    continue
                path = os.path.join(dirpath, name)
                try:
                    with open(path, encoding="utf-8", errors="replace") as fh:
                        body = fh.read()
                except OSError:
                    continue
                rel = os.path.relpath(path, root).replace(os.sep, "/")
                for m in sck__READ.finditer(body):
                    site = "%s:%d" % (rel, body[:m.start()].count("\n") + 1)
                    hits.setdefault((m.group(1), m.group(2)), []).append(site)
    return {k: sorted(set(v)) for k, v in hits.items()}

def sck_resolve(data: dict, dotted: str):
    """The table at a dotted path, or None."""
    cur = data
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur

def sck_declared_elsewhere(data: dict, key: str) -> list:
    """Every dotted path in the SSOT that declares this key name."""
    out = []

    def walk(table, path):
        for k, v in table.items():
            if k == key:
                out.append(".".join(path + [k]))
            if isinstance(v, dict):
                walk(v, path + [k])

    walk(data, [])
    return sorted(out)

def sck_register(data: dict) -> list:
    """[ssot_consumers].unresolved, in declaration order."""
    reg = (data.get("ssot_consumers") or {}).get("unresolved")
    if reg is None:
        return []
    return [str(x).strip() for x in reg if str(x).strip()]

def sck_max_unresolved(data: dict):
    val = (data.get("ssot_consumers") or {}).get("max_unresolved")
    return val if isinstance(val, int) else None

def sck_unresolved(data: dict, root: str) -> dict:
    """{'table.key': (sites, elsewhere)} for every read that resolves to nothing."""
    out = {}
    for (table, key), sites in sck_consumer_reads(root).items():
        target = sck_resolve(data, table)
        if isinstance(target, dict) and key in target:
            continue
        out["%s.%s" % (table, key)] = (sites, sck_declared_elsewhere(data, key))
    return out

def sck_violations(data: dict, root: str) -> list:
    viol = []
    reads = sck_consumer_reads(root)
    if not reads:
        return ["no _toml_section(...).get(...) reads found at all -- the gate "
                "would pass vacuously over an empty set"]

    table = data.get("ssot_consumers")
    if table is None:
        return ["[ssot_consumers] is absent -- nothing bounds how many config keys "
                "a consumer may read that the SSOT does not declare"]
    if "unresolved" not in table:
        viol.append("[ssot_consumers] declares no `unresolved` key -- an implied "
                    "empty register is indistinguishable from a forgotten one")

    reg = sck_register(data)
    if len(reg) != len(set(reg)):
        dupes = sorted({x for x in reg if reg.count(x) > 1})
        viol.append("[ssot_consumers].unresolved lists a pair twice: %s" % ", ".join(dupes))
    if reg != sorted(reg):
        viol.append("[ssot_consumers].unresolved is not sorted -- an unsorted "
                    "register hides an addition inside a reordering")

    found = sck_unresolved(data, root)
    for pair in sorted(set(found) - set(reg)):
        sites, elsewhere = found[pair]
        if elsewhere:
            viol.append("%s is read at %s but the SSOT declares that key at %s -- "
                        "the consumer takes its compiled default and nobody is told"
                        % (pair, sites[0], "/".join(elsewhere)))
        else:
            viol.append("%s is read at %s and is declared NOWHERE in the SSOT"
                        % (pair, sites[0]))

    for pair in sorted(set(reg) - set(found)):
        if pair.rsplit(".", 1)[0] not in {t for t, _ in reads}:
            viol.append("[ssot_consumers].unresolved names '%s', which no shipped "
                        "consumer reads -- drop it" % pair)
        else:
            viol.append("[ssot_consumers].unresolved names '%s', which resolves now "
                        "-- drop it from the register; the register only shrinks" % pair)

    ceiling = sck_max_unresolved(data)
    if ceiling is None:
        viol.append("[ssot_consumers].max_unresolved is unset -- without a ceiling "
                    "the register absorbs new breakage as fast as it appears")
    elif len(reg) > ceiling:
        viol.append("[ssot_consumers].unresolved holds %d entries, over the ratchet "
                    "ceiling max_unresolved = %d. The ceiling only comes DOWN"
                    % (len(reg), ceiling))
    elif len(reg) < ceiling:
        viol.append("[ssot_consumers].unresolved holds %d entries but max_unresolved "
                    "is still %d -- lower it to %d so the ground gained is held"
                    % (len(reg), ceiling, len(reg)))
    return viol

def sck_main() -> int:
    root = os.environ.get("MIOS_DRIFT_ROOT") or os.environ.get("MIOS_ROOT") or "."
    path = os.path.join(root, sck_TOML)
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except OSError as exc:
        print("check-ssot-consumer-keys: cannot read %s: %s" % (path, exc),
              file=sys.stderr)
        return 1

    viol = sck_violations(data, root)
    if viol:
        for v in viol:
            print("check_ssot_consumer_keys: %s" % v, file=sys.stderr)
        return 1

    reads = sck_consumer_reads(root)
    reg = sck_register(data)
    print("[check-ssot-consumer-keys] %d consumer read(s) of %d distinct SSOT key(s); "
          "%d unresolved and registered (ceiling %s)."
          % (sum(len(v) for v in reads.values()), len(reads), len(reg),
             sck_max_unresolved(data)))
    return 0


"""Gate: the [units] projection's debt register is real, sorted and shrinking."""

import os
import shutil
import subprocess
import sys

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover -- py<3.11
    import tomli as tomllib  # type: ignore

up_TOML = "usr/share/mios/mios.toml"
up_UNIT_DIR = "usr/lib/systemd/system"

def up_declared_units(data: dict) -> set:
    """Unit filenames [units.*] projects. Table-valued keys only -- the
    string-valued half is name aliases, not units. See tasks.jsonl task_267."""
    return {k for k, v in (data.get("units") or {}).items() if isinstance(v, dict)}

def up_unit_aliases(data: dict) -> set:
    """The string-valued half of [units]: name -> unit-file aliases."""
    return {k for k, v in (data.get("units") or {}).items() if isinstance(v, str)}

def up_register(data: dict) -> list:
    """[unit_projection].drift, in declaration order."""
    reg = (data.get("unit_projection") or {}).get("drift")
    if reg is None:
        return []
    return [str(x).strip() for x in reg if str(x).strip()]

def up_max_drift(data: dict):
    """The ratchet ceiling, or None when the table declares none."""
    val = (data.get("unit_projection") or {}).get("max_drift")
    return val if isinstance(val, int) else None

def up_shipped(root: str) -> set:
    """Unit files actually on disk."""
    d = os.path.join(root, up_UNIT_DIR)
    if not os.path.isdir(d):
        return set()
    res = set()
    for dp, _, fns in os.walk(d):
        for f in fns:
            rel = os.path.relpath(os.path.join(dp, f), d).replace(os.sep, "/")
            res.add(rel)
    return res

def up_hygiene(data: dict, root: str) -> list:
    """Everything about the register that can be checked without rendering."""
    viol = []
    units = up_declared_units(data)
    if not units:
        return ["[units.*] declares no units at all -- the projection gate would "
                "pass vacuously over an empty set"]

    table = data.get("unit_projection")
    if table is None:
        return ["[unit_projection] is absent -- the [units] projection has no debt "
                "register, so nothing bounds how far the declarations may drift"]
    if "drift" not in table:
        viol.append("[unit_projection] declares no `drift` key -- an implied empty "
                    "register is indistinguishable from a forgotten one")

    reg = up_register(data)
    if len(reg) != len(set(reg)):
        dupes = sorted({x for x in reg if reg.count(x) > 1})
        viol.append("[unit_projection].drift lists a unit twice: %s" % ", ".join(dupes))
    if reg != sorted(reg):
        viol.append("[unit_projection].drift is not sorted -- an unsorted register "
                    "hides an addition inside a reordering")

    on_disk = up_shipped(root)
    for name in sorted(set(reg)):
        if name not in units:
            viol.append("[unit_projection].drift names '%s', which [units.*] does "
                        "not declare -- a unit outside the projection cannot drift "
                        "from it" % name)
        elif name not in on_disk:
            viol.append("[unit_projection].drift names '%s', which the tree does "
                        "not ship" % name)

    ceiling = up_max_drift(data)
    if ceiling is None:
        viol.append("[unit_projection].max_drift is unset -- without a ceiling the "
                    "register can absorb new drift as fast as it is created")
    elif len(reg) > ceiling:
        viol.append("[unit_projection].drift holds %d entries, over the ratchet "
                    "ceiling max_drift = %d. The ceiling only comes DOWN: fix the "
                    "declaration instead of raising it" % (len(reg), ceiling))
    elif len(reg) < ceiling:
        viol.append("[unit_projection].drift holds %d entries but max_drift is "
                    "still %d -- lower the ceiling to %d so the ground gained is "
                    "held" % (len(reg), ceiling, len(reg)))
    return viol

def up__built(root: str):
    catalog = os.environ.get("MIOS_NATIVE_BIN_DIR")
    if catalog:
        p = os.path.join(catalog, "mios-unit-gen" + (".exe" if sys.platform == "win32" else ""))
        if not os.path.isfile(p) or not os.access(p, os.X_OK):
            raise RuntimeError("configured unit generator is missing or not executable: %s" % p)
        return p
    rels = ("target/release/mios-unit-gen.exe", "target/debug/mios-unit-gen.exe", "target/release/mios-unit-gen", "target/debug/mios-unit-gen") if sys.platform == "win32" else ("target/release/mios-unit-gen", "target/debug/mios-unit-gen", "target/release/mios-unit-gen.exe", "target/debug/mios-unit-gen.exe")
    for rel in rels:
        p = os.path.join(root, "tools/native", rel)
        if os.path.isfile(p) and os.access(p, os.X_OK):
            if sys.platform != "win32" and rel.endswith(".exe"):
                continue
            return p
    return None

def up_binary_path(root: str, build: bool = True):
    """A built mios-unit-gen, building it once if cargo is available.

    Without this the gate SKIPS its rendering comparison wherever nobody has run
    cargo -- which is every CI checkout, the one place it matters. See task_267.
    """
    found = up__built(root)
    if found or not build:
        return found
    if not shutil.which("cargo"):
        return None
    try:
        subprocess.run(["cargo", "build", "--manifest-path",
                        os.path.join(root, "tools/native/Cargo.toml"),
                        "-p", "mios-unit-gen"],
                       capture_output=True, timeout=600)
    except (OSError, subprocess.SubprocessError):
        return None
    return up__built(root)

def up_run_binary(path: str, root: str):
    """(ok, output). The binary owns the rendering comparison; we only relay it."""
    env = dict(os.environ, MIOS_ROOT=os.path.abspath(root))
    try:
        proc = subprocess.run([path, "--check"], env=env, capture_output=True,
                              text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, "mios-unit-gen --check could not run: %s" % exc
    out = (proc.stdout + proc.stderr).strip()
    return proc.returncode == 0, out

def up_main() -> int:
    root = os.environ.get("MIOS_DRIFT_ROOT") or os.environ.get("MIOS_ROOT") or "."
    path = os.path.join(root, up_TOML)
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except OSError as exc:
        print("check-unit-projection: cannot read %s: %s" % (path, exc), file=sys.stderr)
        return 1

    viol = up_hygiene(data, root)

    try:
        binary = up_binary_path(root)
    except RuntimeError as exc:
        print("check_unit_projection: %s" % exc, file=sys.stderr)
        return 1
    if binary:
        ok, out = up_run_binary(binary, root)
        if not ok:
            for line in out.splitlines():
                if line.strip():
                    viol.append(line.strip())
    elif os.environ.get("MIOS_DRIFT_REQUIRE_TOOLS", "0") == "1":
        viol.append("no built mios-unit-gen, so the rendering half did not run "
                    "and a drifting unit dropped from the register would pass "
                    "(MIOS_DRIFT_REQUIRE_TOOLS=1). Build it: "
                    "cd tools/native && cargo build -p mios-unit-gen")
    if viol:
        for v in viol:
            print("check_unit_projection: %s" % v, file=sys.stderr)
        return 1

    reg, units = up_register(data), up_declared_units(data)
    note = ("mios-unit-gen --check agrees" if binary else
            "NOT rendering-checked here: no mios-unit-gen and no cargo to build one. "
            "tools/native/mios-unit-gen/tests/projection.rs is the authority and CI runs it")
    print("[check-unit-projection] %d unit(s) declared in [units.*] (plus %d name "
          "aliases sharing the table), %d registered as drifted (ceiling %s). %s."
          % (len(units), len(up_unit_aliases(data)), len(reg), up_max_drift(data), note))
    return 0


"""Gate: a literal beside a MIOS_PORT_ name must be that port's value."""

import os
import re
import sys

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover -- py<3.11
    import tomli as tomllib  # type: ignore

pf_TOML = "usr/share/mios/mios.toml"

# Roots that can BIND or DIAL a port at runtime.
pf_ROOTS = ("usr/lib/systemd/system", "usr/share/containers/systemd",
         "usr/libexec/mios", "usr/lib/mios", "usr/bin", "automation")

# Never scanned: generated projections restate every value by construction,
# docs are check_doc_port_scheme's job, and a .md/.json is not a binder.
pf_SKIP_SUBSTR = ("/reference/", "/doc/", "__pycache__", "/.git/", "manifest.json",
               "names.generated", "referenced_names", "globals.sh", "globals.ps1")
pf_SKIP_EXT = (".md", ".json", ".tsv", ".txt", ".rmeta", ".pyc")

pf_PATTERNS = (
    re.compile(r"MIOS_(?:PORTS?)_([A-Z0-9_]+)\s*:[-=]\s*(\d+)"),        # ${X:-N} / ${X:=N}
    re.compile(r'"MIOS_(?:PORTS?)_([A-Z0-9_]+)"\s*,\s*"(\d+)"'),        # get("X", "N")
    re.compile(r"'MIOS_(?:PORTS?)_([A-Z0-9_]+)'\s*,\s*'(\d+)'"),
    re.compile(r"'MIOS_(?:PORTS?)_([A-Z0-9_]+)'\s+(\d+)"),              # _MiosPort 'X' N
    re.compile(r"^\s*Environment=MIOS_(?:PORTS?)_([A-Z0-9_]+)=(\d+)\s*$"),
    # `get(K, "N") or M` / `get(K) or "M"` -- the SECOND literal is the one that
    # actually runs when the variable is unset or empty, and the first sweep
    # missed it entirely.
    re.compile(r"MIOS_(?:PORTS?)_([A-Z0-9_]+)[\"']?\s*[,)][^\n]{0,60}?\bor\s+[\"']?(\d+)"),
    # The MIOS_<KEY>_PORT spelling: a second emitted name for the same value, so
    # a stale literal beside it is the same defect one alias removed.
    re.compile(r"MIOS_([A-Z0-9_]+)_PORT[\"']?\s*,\s*[\"']?(\d+)"),
)

pf_COMMENT = re.compile(r"^\s*(#|//|--|;)")

def pf_ports_map(data: dict) -> dict:
    """{KEY: value} for every numeric [ports] entry."""
    return {str(k).upper(): v for k, v in (data.get("ports") or {}).items()
            if isinstance(v, int)}

def pf_scan_paths(root: str):
    """Every file under ROOTS that could bind or dial a port."""
    for rel in pf_ROOTS:
        base = os.path.join(root, rel)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames
                           if d not in ("__pycache__", "target", "node_modules")]
            for name in sorted(filenames):
                p = os.path.join(dirpath, name)
                r = os.path.relpath(p, root).replace(os.sep, "/")
                if any(s in "/" + r for s in pf_SKIP_SUBSTR):
                    continue
                if r.endswith(pf_SKIP_EXT):
                    continue
                yield p, r

def pf_findings(data: dict, root: str) -> dict:
    """{'path:KEY': 'literal N, SSOT M'} for every disagreeing literal."""
    ports, out = pf_ports_map(data), {}
    for path, rel in pf_scan_paths(root):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                body = fh.read()
        except OSError:
            continue
        # NOT "MIOS_PORT_": the MIOS_<KEY>_PORT alias spelling would skip the
        # whole file, which is how mios-daemon and mios-pc-control stayed hidden.
        if "MIOS_" not in body:
            continue
        for line in body.splitlines():
            if pf_COMMENT.match(line):
                continue
            for pat in pf_PATTERNS:
                for m in pat.finditer(line):
                    key, lit = m.group(1), int(m.group(2))
                    want = ports.get(key)
                    if want is not None and lit != want:
                        out["%s:%s" % (rel, key)] = "%d, SSOT says %d" % (lit, want)
    return out

def pf_register(data: dict) -> list:
    """The shrink-only debt register, in declaration order."""
    reg = (data.get("ports") or {}).get("stale_fallbacks") or []
    return [str(x).strip() for x in reg if str(x).strip()]

def pf_classify(data: dict, root: str = ".") -> list:
    found, reg = pf_findings(data, root), pf_register(data)
    reg_set, viol = set(reg), []
    if len(reg) != len(reg_set):
        dupes = sorted({k for k in reg if reg.count(k) > 1})
        viol.append("[ports].stale_fallbacks lists an entry twice: %s" % ", ".join(dupes))
    for entry in sorted(set(found) - reg_set):
        viol.append("%s pairs MIOS_PORT_%s with %s -- a literal beside the name is "
                    "the hardcode the SSOT exists to replace"
                    % (entry.rsplit(":", 1)[0], entry.rsplit(":", 1)[1], found[entry]))
    for entry in sorted(reg_set - set(found)):
        viol.append("[ports].stale_fallbacks still lists '%s', which now agrees with "
                    "the SSOT or no longer exists -- the register only shrinks" % entry)
    return viol

def pf_main() -> int:
    root = os.environ.get("MIOS_DRIFT_ROOT") or os.environ.get("MIOS_ROOT") or "."
    path = os.path.join(root, pf_TOML)
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except OSError as exc:
        print("check-port-fallbacks: cannot read %s: %s" % (path, exc), file=sys.stderr)
        return 1
    if not pf_ports_map(data):
        print("check-port-fallbacks: [ports] is empty -- the gate would pass "
              "vacuously", file=sys.stderr)
        return 1

    viol = pf_classify(data, root)
    if viol:
        for v in viol:
            print("check_port_fallbacks: %s" % v, file=sys.stderr)
        return 1
    reg = pf_register(data)
    scanned = sum(1 for _ in pf_scan_paths(root))
    print("[check-port-fallbacks] %d file(s) scanned; every MIOS_PORT_* literal "
          "matches [ports]%s" % (scanned,
          "" if not reg else " or is one of %d registered as shrink-only debt" % len(reg)))
    return 0


"""Gate: an allocated port is bound by something, or registered as not yet wired."""

import os
import re
import subprocess
import sys

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover -- py<3.11
    import tomli as tomllib  # type: ignore

pb_TOML = "usr/share/mios/mios.toml"

# Surfaces that only DESCRIBE ports never prove one is bound: the SSOT itself,
# documentation, generated projections and the task ledgers.
pb_SKIP_PREFIXES = (
    "usr/share/doc/",
    "usr/share/mios/reference/",
    "usr/share/mios/mios.toml",
    "usr/share/mios/names.generated.txt",
    "usr/share/mios/referenced_names.txt",
    "automation/lib/globals.sh",
    "automation/lib/globals.ps1",
    "automation/manifest.json",
    "tools/manifest.json",
    "docs/",
    "ROADMAP.md",
    "tasks.jsonl",
    "ADR.md",
)

def pb_port_keys(data: dict) -> set:
    """Numeric [ports] keys. stack_id is an offset, not a port."""
    ports = data.get("ports") or {}
    return {k for k, v in ports.items() if isinstance(v, int) and k != "stack_id"}

def pb_register(data: dict) -> list:
    """The shrink-only unbound register, in declaration order."""
    reg = (data.get("ports") or {}).get("unbound") or []
    return [str(x).strip() for x in reg if str(x).strip()]

def pb__tracked_files(root: str) -> list:
    out = subprocess.run(["git", "-C", root, "ls-files"],
                         capture_output=True, text=True, check=False).stdout
    return [f for f in out.split("\n") if f and not f.startswith(pb_SKIP_PREFIXES)]

def pb_referenced_ports(root: str, keys: set) -> set:
    """Port keys whose MIOS_PORT_<KEY> appears in a file that could bind or dial it."""
    wanted = {k.upper(): k for k in keys}
    pattern = re.compile(r"\bMIOS_(?:PORTS?)_([A-Z0-9_]+)\b")
    found = set()
    for rel in pb__tracked_files(root):
        path = os.path.join(root, rel)
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                body = fh.read()
        except OSError:
            continue
        for m in pattern.finditer(body):
            key = wanted.get(m.group(1))
            if key:
                found.add(key)
    return found

def pb_classify(data: dict, referenced: set) -> list:
    """Return the violations; empty means every allocated port is accounted for."""
    viol = []
    keys = pb_port_keys(data)
    if not keys:
        return ["[ports] declares no numeric port -- the gate would pass "
                "vacuously over an empty set"]

    reg = pb_register(data)
    reg_set = set(reg)

    if len(reg) != len(reg_set):
        dupes = sorted({k for k in reg if reg.count(k) > 1})
        viol.append("[ports].unbound lists a key twice: %s" % ", ".join(dupes))

    for k in sorted(reg_set - keys):
        viol.append("[ports].unbound names '%s', which is not a [ports] key" % k)

    for k in sorted(reg_set & referenced):
        viol.append("port '%s' IS referenced now but still sits in [ports].unbound "
                    "-- the register only shrinks, so remove it" % k)

    for k in sorted(keys - referenced - reg_set):
        viol.append("port '%s' is allocated but no Quadlet, unit or program "
                    "references MIOS_PORT_%s -- the collision check guards a number "
                    "nothing binds" % (k, k.upper()))

    return viol

def pb_main() -> int:
    root = os.environ.get("MIOS_DRIFT_ROOT") or os.environ.get("MIOS_ROOT") or "."
    path = os.path.join(root, pb_TOML)
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except OSError as exc:
        print("check-ports-bound: cannot read %s: %s" % (path, exc), file=sys.stderr)
        return 1

    keys = pb_port_keys(data)
    referenced = pb_referenced_ports(root, keys)
    viol = pb_classify(data, referenced)
    if viol:
        for v in viol:
            print("check_ports_bound: %s" % v, file=sys.stderr)
        return 1

    print("[check-ports-bound] %d port(s): %d referenced by a consumer, %d "
          "registered unbound" % (len(keys), len(referenced & keys),
                                  len(pb_register(data))))
    return 0


import os
import re
import sys

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

vr_STATUSES = ("shipping", "partial", "design")
vr_REQUIRED = ("title", "summary", "target", "config", "archetype", "artifacts",
            "doc", "status")

def vr_main() -> int:
    root = os.environ.get("MIOS_DRIFT_ROOT") or os.environ.get("MIOS_ROOT") or os.getcwd()
    with open(os.path.join(root, "usr/share/mios/mios.toml"), "rb") as fh:
        ssot = tomllib.load(fh)

    viol = []
    variants = ssot.get("variants") or {}
    entries = variants.get("entries") or {}
    naming = variants.get("naming") or {}
    if not entries:
        print("[variants.entries] is empty -- a registry with no entries passes"
              " every check it has and states nothing")
        return 1

    editions = ssot.get("editions") or {}
    archetypes = ((ssot.get("blade") or {}).get("archetypes") or {})
    # [deploy.formats] is the matrix; a format needs a build target, not
    # necessarily a recipe file, so the recipe directory is the wrong authority.
    recipes = {k for k, v in ((ssot.get("deploy") or {}).get("formats") or {}).items()
               if isinstance(v, dict)}

    key_re = re.compile(r"^[%s]+$" % naming.get("key_charset", "a-z0-9-"))
    prefix = naming.get("prefix", "MiOS")
    sep = naming.get("separator", "-")
    base = naming.get("base", "mios")

    claimed = set()
    for key, spec in sorted(entries.items()):
        where = "[variants.entries.%s]" % key
        for field in vr_REQUIRED:
            if field not in spec:
                viol.append("%s is missing %s" % (where, field))
        if not key_re.match(key):
            viol.append("%s key breaks %s" % (where, naming.get("key_pattern", "")))

        title = str(spec.get("title", ""))
        if key == base:
            if title != prefix:
                viol.append("%s the base variant is titled %r, expected %r"
                            % (where, title, prefix))
        elif not title.startswith(prefix + sep):
            viol.append("%s title %r does not follow %s"
                        % (where, title, naming.get("title_pattern", "")))
        elif title.lower() != key:
            viol.append("%s title %r and key %r are not the same name in two"
                        " registers" % (where, title, key))

        status = spec.get("status")
        if status not in vr_STATUSES:
            viol.append("%s status %r is not one of %s" % (where, status, list(vr_STATUSES)))

        for table in spec.get("config") or []:
            if table not in ssot:
                viol.append("%s config names [%s], which the SSOT does not define"
                            % (where, table))
        ed = spec.get("edition")
        if ed:
            claimed.add(ed)
            if ed not in editions:
                viol.append("%s edition %r is not in [editions]" % (where, ed))
        arch = spec.get("archetype")
        if arch and arch not in archetypes:
            viol.append("%s archetype %r is not in [blade.archetypes]" % (where, arch))
        for art in spec.get("artifacts") or []:
            if art not in recipes:
                viol.append("%s artifact %r is not a declared deployment format"
                            % (where, art))
        doc = spec.get("doc")
        if doc and not os.path.isfile(os.path.join(root, doc)):
            viol.append("%s doc %s does not exist" % (where, doc))

    for ed in sorted(editions):
        if ed not in claimed:
            viol.append("[editions.%s] is claimed by no variant -- an edition"
                        " nobody ships is configuration for nothing" % ed)

    ceiling = variants.get("max_design_variants")
    design = [k for k, v in entries.items() if v.get("status") == "design"]
    if ceiling is None:
        viol.append("[variants] has no max_design_variants -- an absent ceiling"
                    " lets a design doc stay the deliverable")
    elif len(design) > int(ceiling):
        viol.append("variants still in design %d > ceiling %d: %s"
                    % (len(design), ceiling, sorted(design)))

    print("\n".join(viol))
    if viol:
        return 1
    print("[check-variant-registry] %d variant(s); %d shipping, %d partial, %d design"
          % (len(entries),
             sum(1 for v in entries.values() if v.get("status") == "shipping"),
             sum(1 for v in entries.values() if v.get("status") == "partial"),
             len(design)), file=sys.stderr)
    return 0


import os
import re
import sys

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

df_STATUSES = ("shipping", "partial", "design")
# "artifacts" is the glob list the verifier requires a match for. A format
# without one is a format the publish gate cannot notice the absence of, which
# is how an empty build tree came to satisfy it; only the registry format,
# which writes no file, is allowed an empty list.
df_REQUIRED = ("title", "summary", "target", "recipe", "medium", "gui", "status",
            "artifacts")
# Targets in the Justfile that orchestrate or post-process rather than produce a
# deployable artifact. Listed so that a NEW artifact target cannot hide here.
df_NOT_A_FORMAT = frozenset({
    "build", "build-logged", "build-verbose", "all", "publish", "rechunk",
    "rechunk-conv", "artifact", "sbom", "verify-images", "cloud-build",
    "embed-log", "log-bootstrap", "build-and-log", "all-bootstrap",
})

def df_main() -> int:
    root = os.environ.get("MIOS_DRIFT_ROOT") or os.environ.get("MIOS_ROOT") or os.getcwd()
    with open(os.path.join(root, "usr/share/mios/mios.toml"), "rb") as fh:
        ssot = tomllib.load(fh)

    viol = []
    deploy = ssot.get("deploy") or {}
    formats = {k: v for k, v in (deploy.get("formats") or {}).items()
               if isinstance(v, dict)}
    if not formats:
        print("[deploy.formats] is empty -- a matrix with no entries supports"
              " nothing and passes every check it has")
        return 1

    just = ""
    jpath = os.path.join(root, "Justfile")
    if os.path.isfile(jpath):
        just = open(jpath, encoding="utf-8", errors="replace").read()
    else:
        viol.append("Justfile is missing -- no format can be built")
    targets = set(re.findall(r"^([a-z0-9][a-z0-9_-]*):", just, re.M))

    claimed_recipes = set()
    for name, spec in sorted(formats.items()):
        where = "[deploy.formats.%s]" % name
        for field in df_REQUIRED:
            if field not in spec:
                viol.append("%s is missing %s" % (where, field))
        if not spec.get("artifacts") and spec.get("medium") != "container registry":
            viol.append("%s declares no artifacts globs, so a build that produces"
                        " no %s file passes verify-images unnoticed"
                        % (where, name))
        if spec.get("status") not in df_STATUSES:
            viol.append("%s status %r is not one of %s"
                        % (where, spec.get("status"), list(df_STATUSES)))
        target = spec.get("target")
        if target and target not in targets:
            viol.append("%s names target %r, which the Justfile does not define"
                        % (where, target))
        recipe = spec.get("recipe")
        if recipe:
            claimed_recipes.add(os.path.basename(recipe))
            if not os.path.isfile(os.path.join(root, recipe)):
                viol.append("%s recipe %s does not exist" % (where, recipe))

    shared = (deploy.get("formats") or {}).get("shared_recipe")
    if shared:
        claimed_recipes.add(os.path.basename(shared))
        if not os.path.isfile(os.path.join(root, shared)):
            viol.append("[deploy.formats].shared_recipe %s does not exist" % shared)

    art_dir = os.path.join(root, "config/artifacts")
    if os.path.isdir(art_dir):
        for fn in sorted(os.listdir(art_dir)):
            if fn.endswith(".toml") and fn not in claimed_recipes:
                viol.append("config/artifacts/%s is claimed by no format -- a"
                            " recipe nothing builds from is configuration for"
                            " nothing" % fn)

    # A target that produces an artifact and is not declared is an unsupported
    # format shipping anyway, which is the half of the matrix nobody maintains.
    declared_targets = {s.get("target") for s in formats.values()}
    for t in sorted(targets):
        if t in df_NOT_A_FORMAT or t in declared_targets:
            continue
        body = re.search(r"^%s:.*?(?=^[a-z0-9][a-z0-9_-]*:|\Z)" % re.escape(t),
                         just, re.M | re.S)
        # Creating the output directory is what a producing target does; a
        # status target merely reads the same paths, and matching a mention
        # rather than a write reported flight-status as an undeclared format.
        if body and re.search(r"mkdir\s+-p\s+build/", body.group(0)):
            viol.append("Justfile target %r writes a deployable artifact and is"
                        " in no [deploy.formats] entry" % t)

    for vname, vspec in sorted((ssot.get("variants") or {}).get("entries", {}).items()):
        for art in vspec.get("artifacts") or []:
            if art not in formats:
                viol.append("[variants.entries.%s] ships %r, which [deploy.formats]"
                            " does not define" % (vname, art))

    print("\n".join(viol))
    if viol:
        return 1
    shipping = sum(1 for s in formats.values() if s.get("status") == "shipping")
    print("[check-deploy-formats] %d format(s), %d shipping; every target and"
          " recipe resolves" % (len(formats), shipping), file=sys.stderr)
    return 0


"""Gate: the blade role is stated once, legally, and in one place."""

import os
import re
import sys

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover -- py<3.11
    import tomli as tomllib  # type: ignore

rs_TOML = "usr/share/mios/mios.toml"
rs_UNIT_DIR = "usr/lib/systemd/system"

# Both halves of the resolver twin. Neither may keep the retired names alive.
rs_KEEP_LISTS = ("usr/lib/mios/mios_toml.py",
              "tools/native/mios-ssot-walk/src/lib.rs")
rs_RETIRED_NAMES = ("MIOS_PROFILE_ROLE", "MIOS_PROFILE_FEATURES")

# Executable blade code: a re-introduced `case "$ROLE" in hybrid) ...` here is
# a second copy of [blade.archetypes].
rs_BLADE_CODE = ("usr/lib/mios/blade.sh",
              "usr/libexec/mios/role-apply",
              "usr/libexec/mios/mios-blade")

def rs_archetypes(data: dict) -> dict:
    """{name: [capability, ...]} from [blade.archetypes]."""
    out = {}
    for name, caps in ((data.get("blade") or {}).get("archetypes") or {}).items():
        if isinstance(caps, str):
            caps = [caps]
        out[str(name)] = [str(c).strip() for c in (caps or []) if str(c).strip()]
    return out

def rs_aliases(data: dict) -> dict:
    """{legacy-spelling: archetype} from [blade.role_aliases]."""
    return {str(k): str(v)
            for k, v in ((data.get("blade") or {}).get("role_aliases") or {}).items()}

def rs_role_targets(data: dict) -> list:
    """The unit each archetype's name derives, in [blade.archetypes] order."""
    return ["mios-%s.target" % name for name in sorted(rs_archetypes(data))]

def rs_unit_body(root: str, name: str) -> str:
    try:
        with open(os.path.join(root, rs_UNIT_DIR, name), encoding="utf-8",
                  errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""

def rs_check_type(data: dict) -> list:
    arche = rs_archetypes(data)
    btype = str((data.get("blade") or {}).get("type") or "").strip()
    if not btype:
        return ["[blade].type is empty -- the image would have no archetype"]
    if not arche:
        return ["[blade.archetypes] is empty -- the gate would pass vacuously"]
    if btype not in arche:
        return ["[blade].type is '%s', which is not an archetype (declared: %s)"
                % (btype, ", ".join(sorted(arche)))]
    return []

def rs_check_targets(data: dict, root: str) -> list:
    viol = []
    for name in sorted(rs_archetypes(data)):
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name):
            viol.append("archetype '%s' is not a legal unit-name stem -- it derives "
                        "mios-%s.target" % (name, name))
        unit = "mios-%s.target" % name
        if not os.path.isfile(os.path.join(root, rs_UNIT_DIR, unit)):
            viol.append("archetype '%s' derives %s, which is not a shipped unit -- "
                        "role-apply would set-default a target that does not exist"
                        % (name, unit))
    return viol

def rs_check_capabilities_consumed(data: dict) -> list:
    """Every capability an archetype grants must be required by some unit --
    the reverse of check_blade_coverage, which proves the forward direction."""
    granted, viol = set(), []
    for caps in ((data.get("blade") or {}).get("archetypes") or {}).values():
        if isinstance(caps, str):
            caps = [caps]
        granted |= {str(c).strip() for c in (caps or []) if str(c).strip()}
    required = set()
    for caps in ((data.get("blade") or {}).get("requires") or {}).values():
        if isinstance(caps, str):
            caps = [caps]
        required |= {str(c).strip() for c in (caps or []) if str(c).strip()}
    for cap in sorted(granted - required):
        viol.append("capability '%s' is granted by an archetype but required by "
                    "NO unit -- an archetype that grants only it is a duplicate "
                    "of one that grants nothing" % cap)
    return viol

def rs_check_aliases(data: dict) -> list:
    viol, arche = [], rs_archetypes(data)
    for legacy, target in sorted(rs_aliases(data).items()):
        if target not in arche:
            viol.append("[blade.role_aliases].%s points at '%s', which is not an "
                        "archetype" % (legacy, target))
        if legacy in arche:
            viol.append("[blade.role_aliases].%s shadows an archetype of the same "
                        "name -- one spelling, one meaning (Law 9)" % legacy)
    return viol

def rs_check_conflicts(data: dict, root: str) -> list:
    """Role targets must conflict pairwise: they are reached by `systemctl
    start`, not by isolation, so a missing edge leaves the old role active."""
    viol, targets = [], rs_role_targets(data)
    if len(targets) < 2:
        return viol
    for unit in targets:
        body = rs_unit_body(root, unit)
        if not body:
            continue  # check_targets already reported the missing unit
        m = re.search(r"^Conflicts=(.*)$", body, re.M)
        have = set(m.group(1).split()) if m else set()
        want = set(targets) - {unit}
        missing = sorted(want - have)
        if missing:
            viol.append("%s does not conflict with %s -- switching away from it "
                        "would leave it active" % (unit, ", ".join(missing)))
        stray = sorted(have - want)
        if stray:
            viol.append("%s conflicts with %s, which is not a role target"
                        % (unit, ", ".join(stray)))
    return viol

def rs_check_aliases_in_units(root: str) -> list:
    """An Alias= must carry the same suffix as the unit itself; systemd cannot
    install one that does not, leaving the unit with no [Install] at all."""
    viol = []
    unit_dir = os.path.join(root, rs_UNIT_DIR)
    if not os.path.isdir(unit_dir):
        return viol
    for name in sorted(os.listdir(unit_dir)):
        path = os.path.join(unit_dir, name)
        if not os.path.isfile(path) or "." not in name:
            continue
        suffix = "." + name.rsplit(".", 1)[1]
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                body = fh.read()
        except OSError:
            continue
        for m in re.finditer(r"^Alias=(.*)$", body, re.M):
            for alias in m.group(1).split():
                if not alias.endswith(suffix):
                    viol.append("%s declares Alias=%s -- an alias must carry the "
                                "same suffix (%s) as its unit, so systemd cannot "
                                "install it" % (name, alias, suffix))
    return viol

def rs_check_profile_retired(data: dict, root: str) -> list:
    """[profile].role was a second spelling of the archetype, read by nothing.
    It may come back only as a legal alias of [blade].type."""
    viol, arche = [], rs_archetypes(data)
    profile = data.get("profile")
    if isinstance(profile, dict):
        for key in profile:
            if key.lower() == "role":
                val = str(profile[key] or "").strip()
                if val not in arche:
                    viol.append("[profile].%s is '%s', which is not a legal "
                                "[blade].type (declared: %s)"
                                % (key, val, ", ".join(sorted(arche))))
            if key.lower() == "features":
                viol.append("[profile].%s is retired -- blade capabilities are a "
                            "closed set; grant one with `mios blade "
                            "add-capability`" % key)
    for rel in rs_KEEP_LISTS:
        path = os.path.join(root, rel)
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                body = fh.read()
        except OSError:
            continue
        for name in rs_RETIRED_NAMES:
            if name in body:
                viol.append("%s still references %s -- the retired [profile] keys "
                            "must not be resurrected by a keep-list" % (rel, name))
    return viol

def rs_check_no_hardcoded_roles(data: dict, root: str) -> list:
    """The blade code must not restate [blade.archetypes]. No literal is
    permitted: the floor is the generated karg, the demotion target is
    [blade].fallback."""
    viol = []
    names = sorted(rs_archetypes(data))
    # A BLADE_CODE file that is absent yet TRACKED is a deleted deliverable and
    # must not shrink the subject list in silence. Absent and untracked is a
    # fixture root, where skipping is correct -- so the predicate is
    # "tracked here", not "absent".
    import subprocess
    tracked_here = set()
    try:
        _p = subprocess.run(["git", "-C", root, "ls-files", *rs_BLADE_CODE],
                            capture_output=True, text=True, check=False)
        if _p.returncode == 0:
            tracked_here = {l.strip().replace(os.sep, "/")
                            for l in _p.stdout.splitlines() if l.strip()}
    except OSError:
        pass
    for rel in rs_BLADE_CODE:
        path = os.path.join(root, rel)
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
        except OSError as exc:
            if rel in tracked_here:
                viol.append("%s: blade code listed in BLADE_CODE is TRACKED but "
                            "could not be read (%s), so it was never checked "
                            "for a restated archetype" % (rel, exc))
            continue
        in_heredoc = None
        for num, line in enumerate(lines, 1):
            # A heredoc body is not shell control flow. The embedded python that
            # READS [blade.archetypes] necessarily names TOML keys -- `endpoint`
            # is both an archetype and a very ordinary config key -- and flagging
            # that would punish the SSOT read this rule exists to require.
            if in_heredoc is not None:
                if line.strip() == in_heredoc:
                    in_heredoc = None
                continue
            opened = re.search(r"<<-?'?([A-Za-z_][A-Za-z0-9_]*)'?", line)
            if opened:
                in_heredoc = opened.group(1)
                continue
            code = line.split("#", 1)[0]
            for name in names:
                # A token after `.` is a member/key access, not a bare role:
                # `[ai].endpoint` names a TOML key that happens to share a name
                # with an archetype. `mios-endpoint.target` is still caught --
                # only `.` is excluded, never `-`.
                if re.search(r"(?<!\.)\b%s\b" % re.escape(name), code):
                    viol.append("%s:%d spells the archetype '%s' as a literal -- "
                                "the archetype table is [blade.archetypes], not "
                                "code (Law 7)" % (rel, num, name))
    return viol

def rs_collect(data: dict, root: str) -> list:
    return (rs_check_type(data)
            + rs_check_targets(data, root)
            + rs_check_capabilities_consumed(data)
            + rs_check_aliases(data)
            + rs_check_conflicts(data, root)
            + rs_check_aliases_in_units(root)
            + rs_check_profile_retired(data, root)
            + rs_check_no_hardcoded_roles(data, root))

def rs_main() -> int:
    root = os.environ.get("MIOS_DRIFT_ROOT") or os.environ.get("MIOS_ROOT") or "."
    path = os.path.join(root, rs_TOML)
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except OSError as exc:
        print("check-role-ssot: cannot read %s: %s" % (path, exc), file=sys.stderr)
        return 1

    viol = rs_collect(data, root)
    if viol:
        for v in viol:
            print("check_role_ssot: %s" % v, file=sys.stderr)
        return 1

    arche = rs_archetypes(data)
    seats = sorted(n for n, caps in arche.items() if not caps)
    print("[check-role-ssot] %d archetype(s), each with a shipped target and a "
          "complete conflict graph; %d alias(es); seat(s): %s; [blade].type=%s"
          % (len(arche), len(rs_aliases(data)), ", ".join(seats) or "none",
             (data.get("blade") or {}).get("type")))
    return 0


"""Gate: every node in the fan-out pool is a distinct, reachable, honest lane."""

import os
import re
import sys

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover -- py<3.11
    import tomli as tomllib  # type: ignore

np_TOML = "usr/share/mios/mios.toml"

def np_nodes(data: dict) -> dict:
    """{name: cfg} for every declared compute node."""
    return {str(k): v for k, v in (data.get("nodes") or {}).items()
            if isinstance(v, dict)}

def np_lane_vocabulary(data: dict) -> set:
    """Legal lane names, read from [dispatch].lane_priority -- the one place the
    scheduler's buckets are declared."""
    raw = str((data.get("dispatch") or {}).get("lane_priority") or "")
    out = set()
    for part in raw.split(","):
        name = part.split(":", 1)[0].strip()
        if name and not name.startswith("_"):
            out.add(name)
    return out

def np_blades(data: dict) -> set:
    return {str(k) for k in (data.get("blades") or {})}

def np__ep(cfg: dict) -> str:
    return str(cfg.get("endpoint") or "").rstrip("/")

def np_aliases(data: dict) -> list:
    """Two nodes with the same (endpoint, model, lane) are one backend twice."""
    seen, viol = {}, []
    for name, cfg in sorted(np_nodes(data).items()):
        ep = np__ep(cfg)
        if not ep:
            continue  # an empty endpoint is a declared-inert placeholder
        key = (ep, str(cfg.get("model") or ""), str(cfg.get("lane") or ""))
        if key in seen:
            viol.append("[nodes].%s duplicates [nodes].%s exactly (%s) -- the "
                        "fan-out counts one backend as two lanes"
                        % (name, seen[key], key[0]))
        else:
            seen[key] = name
    return viol

def np_lane_conflicts(data: dict) -> list:
    """One endpoint cannot be two lanes: the semaphore bucket would be split."""
    by_ep, viol = {}, []
    for name, cfg in sorted(np_nodes(data).items()):
        ep = np__ep(cfg)
        if ep:
            by_ep.setdefault(ep, []).append((name, str(cfg.get("lane") or "")))
    for ep, entries in sorted(by_ep.items()):
        lanes = {lane for _, lane in entries}
        if len(lanes) > 1:
            viol.append("endpoint %s is declared as %s by %s -- one endpoint, "
                        "one lane" % (ep, "/".join(sorted(lanes)),
                                      ", ".join(n for n, _ in entries)))
    return viol

def np_illegal_lanes(data: dict) -> list:
    vocab, viol = np_lane_vocabulary(data), []
    if not vocab:
        return ["[dispatch].lane_priority declares no lanes -- the gate would "
                "pass vacuously"]
    for name, cfg in sorted(np_nodes(data).items()):
        lane = str(cfg.get("lane") or "").strip()
        if lane and lane not in vocab:
            viol.append("[nodes].%s declares lane '%s', which [dispatch]."
                        "lane_priority does not budget (legal: %s)"
                        % (name, lane, ", ".join(sorted(vocab))))
    return viol

def np_orphan_blades(data: dict) -> list:
    """A node MAY omit `blade` -- it then belongs to the local blade, whose name
    comes from [identity].hostname. Naming one that does not exist is the error."""
    known, viol = np_blades(data), []
    for name, cfg in sorted(np_nodes(data).items()):
        blade = str(cfg.get("blade") or "").strip()
        if blade and blade not in known:
            viol.append("[nodes].%s names blade '%s', which [blades] does not "
                        "declare" % (name, blade))
    return viol

np__LOCAL = re.compile(r"://(?:localhost|127\.0\.0\.1):(\d+)")

def np_unmovable_endpoints(data: dict) -> list:
    """A local endpoint with a baked port cannot be repointed at a blade."""
    viol = []
    for name, cfg in sorted(np_nodes(data).items()):
        ep = np__ep(cfg)
        for m in np__LOCAL.finditer(ep):
            viol.append("[nodes].%s bakes port %s into its endpoint -- an "
                        "/etc/mios overlay cannot move it, so the node can never "
                        "be offloaded" % (name, m.group(1)))
    return viol

def np_classify(data: dict) -> list:
    if not np_nodes(data):
        return ["[nodes] declares no compute node -- the gate would pass "
                "vacuously over an empty pool"]
    return (np_aliases(data) + np_lane_conflicts(data) + np_illegal_lanes(data)
            + np_orphan_blades(data) + np_unmovable_endpoints(data))

def np_main() -> int:
    root = os.environ.get("MIOS_DRIFT_ROOT") or os.environ.get("MIOS_ROOT") or "."
    path = os.path.join(root, np_TOML)
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except OSError as exc:
        print("check-node-pool: cannot read %s: %s" % (path, exc), file=sys.stderr)
        return 1

    viol = np_classify(data)
    if viol:
        for v in viol:
            print("check_node_pool: %s" % v, file=sys.stderr)
        return 1

    n = np_nodes(data)
    live = {np__ep(c) for c in n.values() if np__ep(c)}
    print("[check-node-pool] %d node(s) over %d distinct endpoint(s); lanes %s; "
          "%d declared inert" % (len(n), len(live),
                                 "/".join(sorted(np_lane_vocabulary(data))),
                                 sum(1 for c in n.values() if not np__ep(c))))
    return 0


"""Gate: every service is capability-gated, or registered as ungated core."""

import os
import re
import sys

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover -- py<3.11
    import tomli as tomllib  # type: ignore

bc_TOML = "usr/share/mios/mios.toml"

def bc_containers(data: dict) -> set:
    """Every Quadlet container the SSOT declares."""
    return set(data.get("containers") or {})

def bc_long_running_units(root: str) -> set:
    """Shipped .service units that stay up. A oneshot needs no blade gate: it
    runs, exits, and costs a seat nothing to leave enabled."""
    out = set()
    unit_dir = os.path.join(root, "usr/lib/systemd/system")
    if not os.path.isdir(unit_dir):
        return out
    for name in sorted(os.listdir(unit_dir)):
        if not name.endswith(".service") or "@" in name:
            continue
        try:
            with open(os.path.join(unit_dir, name), encoding="utf-8",
                      errors="replace") as fh:
                body = fh.read()
        except OSError:
            continue
        stype = ""
        for line in body.splitlines():
            if line.startswith("Type="):
                stype = line.split("=", 1)[1].strip()
                break
        if stype != "oneshot":
            out.add(name[:-len(".service")])
    return out

def bc_all_units(data: dict, root: str) -> set:
    """Units that MUST carry a classification: containers and long-running
    services. Containers and native units share ONE namespace -- a Quadlet named
    `x` generates `x.service` -- so one classification covers both spellings."""
    return bc_containers(data) | bc_long_running_units(root)

def bc_known_units(data: dict, root: str) -> set:
    """Every shipped unit stem, any type. Wider than all_units on purpose: a
    oneshot or a target needs no classification of its own, but MAY legitimately
    be gated because it activates something that is."""
    out = set(bc_containers(data))
    unit_dir = os.path.join(root, "usr/lib/systemd/system")
    if os.path.isdir(unit_dir):
        for name in os.listdir(unit_dir):
            if os.path.isfile(os.path.join(unit_dir, name)) and "." in name:
                out.add(name.rsplit(".", 1)[0])
    return out

def bc_seat_side(data: dict) -> list:
    """Units a seat deliberately runs -- a positive claim, not debt."""
    reg = (data.get("blade") or {}).get("seat_side") or []
    return [str(x).strip() for x in reg if str(x).strip()]

# Ordering only. After= does not activate anything, so it never propagates a gate.
bc__PULL_KEYS = ("Requires=", "BindsTo=", "Requisite=", "Wants=")

def bc_unit_pulls(root: str) -> dict:
    """{unit-stem: {dependency-stem, ...}} over every shipped unit of any type.

    Only ACTIVATING dependencies count: a unit that merely orders itself After=
    a gated unit is unaffected when that unit is condition-skipped.
    """
    out = {}
    unit_dir = os.path.join(root, "usr/lib/systemd/system")
    if not os.path.isdir(unit_dir):
        return out
    for name in sorted(os.listdir(unit_dir)):
        path = os.path.join(unit_dir, name)
        if not os.path.isfile(path) or "." not in name:
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                body = fh.read()
        except OSError:
            continue
        deps = set()
        for line in body.splitlines():
            for key in bc__PULL_KEYS:
                if line.startswith(key):
                    for tok in line[len(key):].split():
                        deps.add(tok[:-len(".service")]
                                 if tok.endswith(".service") else tok)
        if deps:
            out[name.rsplit(".", 1)[0]] = deps
    return out

def bc_soft_ok(data: dict) -> list:
    """Units whose pull on a gated unit is soft and that degrade without it."""
    reg = (data.get("blade") or {}).get("soft_ok") or []
    return [str(x).strip() for x in reg if str(x).strip()]

def bc_dependency_violations(data: dict, root: str) -> list:
    """A unit activating a gated unit must carry its capabilities (ADR-0016 D4)."""
    req = bc_requires(data)
    seat = set(bc_seat_side(data)) | set(bc_soft_ok(data))
    viol = []
    for stem, deps in sorted(bc_unit_pulls(root).items()):
        hit = deps & set(req)
        if not hit:
            continue
        need = set().union(*(set(req[h]) for h in hit))
        have = set(req.get(stem, []))
        if stem in seat or need <= have:
            continue
        viol.append("unit '%s' activates %s but is missing their capability %s -- "
                    "it would start where its dependency is condition-skipped"
                    % (stem, "/".join(sorted(hit)),
                       "/".join(sorted(need - have))))
    return viol

def bc_port_namers(data: dict, root: str) -> dict:
    """{port-key: {unit-stem, ...}} over every shipped unit that names a port,
    by MIOS_PORT_<KEY> or by its literal value."""
    ports = {k: v for k, v in (data.get("ports") or {}).items() if isinstance(v, int)}
    out = {k: set() for k in ports}
    for base in ("usr/lib/systemd/system", "usr/share/containers/systemd"):
        d = os.path.join(root, base)
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            path = os.path.join(d, name)
            if not os.path.isfile(path) or "." not in name:
                continue
            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    body = fh.read()
            except OSError:
                continue
            code = "\n".join(l for l in body.splitlines()
                              if not l.lstrip().startswith("#"))
            stem = name.rsplit(".", 1)[0]
            for key, num in ports.items():
                if any((prefix + key.upper()) in code for prefix in ("MIOS_PORTS_", "MIOS_PORT_")) or \
                        re.search(r"(?<![0-9])%d(?![0-9])" % num, code):
                    out[key].add(stem)
    return out

def bc_person_facing(data: dict) -> set:
    """Ports whose client is the human: anything with a browser-openable [urls]
    entry, plus the front door [ai].endpoint resolves. Derived, not declared."""
    out = set()
    for value in ((data.get("urls") or {}).values()):
        if isinstance(value, str):
            out |= {m.lower() for m in
                    re.findall(r"\$\{MIOS_(?:PORTS?)_([A-Z0-9_]+)\}", value)}
    endpoint = str((data.get("ai") or {}).get("endpoint") or "")
    out |= {m.lower() for m in
            re.findall(r"\$\{MIOS_(?:PORTS?)_([A-Z0-9_]+)\}", endpoint)}
    return out

def bc_seat_dead_weight(data: dict, root: str) -> list:
    """A seat-side unit whose port only a gated unit dials is dead weight. The
    coupling is an address, so the dependency walk cannot see it."""
    req, seat = bc_requires(data), set(bc_seat_side(data))
    soft = set(bc_soft_ok(data))
    viol = []
    for key, namers in sorted(bc_port_namers(data, root).items()):
        if key in bc_person_facing(data):
            continue          # the client is the human, not another unit
        binders = namers & seat
        others = namers - seat
        if not binders or not others:
            continue          # nobody else names it: the person is the client
        if others - set(req) - soft:
            continue          # at least one ungated client remains
        viol.append("seat-side %s binds '%s', but every other unit naming it "
                    "(%s) is capability-gated -- on a seat it serves nothing"
                    % ("/".join(sorted(binders)), key, ", ".join(sorted(others))))
    return viol

def bc_requires(data: dict) -> dict:
    """{service: [capability, ...]} from [blade.requires]."""
    out = {}
    for svc, caps in ((data.get("blade") or {}).get("requires") or {}).items():
        if isinstance(caps, str):
            caps = [caps]
        out[svc] = [str(c).strip() for c in (caps or []) if str(c).strip()]
    return out

def bc_archetype_caps(data: dict) -> set:
    """Every capability some archetype can grant."""
    out = set()
    for caps in ((data.get("blade") or {}).get("archetypes") or {}).values():
        if isinstance(caps, str):
            caps = [caps]
        for c in caps or []:
            out.add(str(c).strip())
    return {c for c in out if c}

def bc_register(data: dict) -> list:
    """The shrink-only ungated register, in declaration order."""
    reg = (data.get("blade") or {}).get("ungated") or []
    return [str(x).strip() for x in reg if str(x).strip()]

def bc_classify(data: dict, root: str = ".") -> list:
    """Return the violations; empty means every unit has exactly one answer."""
    viol = []
    units = bc_all_units(data, root)
    if not units:
        return ["no containers and no long-running units found -- the gate would "
                "pass vacuously over an empty set"]

    req = bc_requires(data)
    granted = bc_archetype_caps(data)
    reg, seat = bc_register(data), bc_seat_side(data)
    reg_set, seat_set = set(reg), set(seat)

    for name, lst in (("ungated", reg), ("seat_side", seat)):
        if len(lst) != len(set(lst)):
            dupes = sorted({k for k in lst if lst.count(k) > 1})
            viol.append("[blade].%s lists a unit twice: %s" % (name, ", ".join(dupes)))

    known = bc_known_units(data, root)
    for label, names in (("[blade.requires] gates", set(req)),
                         ("[blade].ungated names", reg_set),
                         ("[blade].seat_side names", seat_set)):
        for svc in sorted(names - known):
            viol.append("%s '%s', which is not a declared container or a shipped "
                        "unit" % (label, svc))

    for svc in sorted((set(req) & reg_set) | (set(req) & seat_set)
                      | (reg_set & seat_set)):
        viol.append("unit '%s' is classified more than once -- gated, seat-side "
                    "and ungated are mutually exclusive" % svc)

    fallbacks = (data.get("blade") or {}).get("cpu_fallbacks") or {}
    for svc, caps in sorted(req.items()):
        if not caps:
            viol.append("[blade.requires].%s lists no capability -- an empty list "
                        "gates nothing, so say so in [blade].seat_side instead" % svc)
        if "gpu-serving" in caps and (svc not in fallbacks or not fallbacks[svc]):
            viol.append("unit '%s' requires 'gpu-serving' but declares no fallback in [blade.cpu_fallbacks] (task_2237)" % svc)
        for cap in caps:
            if cap not in granted:
                viol.append("capability '%s' (required by %s) is granted by NO "
                            "archetype -- nothing could ever activate it" % (cap, svc))

    for svc in sorted(units - set(req) - reg_set - seat_set):
        viol.append("unit '%s' is classified nowhere -- gate it in [blade.requires], "
                    "declare it in [blade].seat_side, or register the debt in "
                    "[blade].ungated" % svc)

    viol.extend(bc_dependency_violations(data, root))
    viol.extend(bc_seat_dead_weight(data, root))
    return viol

def bc_main() -> int:
    root = os.environ.get("MIOS_DRIFT_ROOT") or os.environ.get("MIOS_ROOT") or "."
    path = os.path.join(root, bc_TOML)
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except OSError as exc:
        print("check-blade-coverage: cannot read %s: %s" % (path, exc), file=sys.stderr)
        return 1

    viol = bc_classify(data, root)
    if viol:
        for v in viol:
            print("check_blade_coverage: %s" % v, file=sys.stderr)
        return 1

    must = bc_all_units(data, root)
    req = bc_requires(data)
    print("[check-blade-coverage] %d unit(s) require a classification: %d gated, "
          "%d seat-side, %d registered ungated. %d further unit(s) are gated "
          "because they activate one (oneshots, targets)."
          % (len(must), len(set(req) & must), len(bc_seat_side(data)),
             len(bc_register(data)), len(set(req) - must)))
    return 0


import os
import re
import sys

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover -- py<3.11
    import tomli as tomllib  # type: ignore

fs_TOML = "usr/share/mios/mios.toml"

def fs_fleet_shape(data: dict) -> dict:
    """[blades] min/typical/max node counts."""
    b = data.get("blades") or {}
    return {k: b.get(k) for k in ("min_nodes", "typical_nodes", "max_nodes")}

def fs_archetypes_granting(data: dict, needed) -> list:
    """Archetypes granting EVERY capability in `needed`."""
    blade = data.get("blade") or {}
    out = []
    for name, caps in (blade.get("archetypes") or {}).items():
        if isinstance(caps, str):
            caps = [caps]
        have = {str(c).strip() for c in (caps or [])}
        if set(needed) <= have:
            out.append(name)
    return sorted(out)

def fs_k3s_multi_server(data: dict, root: str):
    """More than one archetype can stand up a k3s control plane, and the unit
    has no join path. Detail string, or None."""
    req = ((data.get("blade") or {}).get("requires") or {}).get("mios-k3s")
    if not req:
        return None
    if isinstance(req, str):
        req = [req]
    grantors = fs_archetypes_granting(data, [str(c).strip() for c in req])
    if len(grantors) < 2:
        return None
    path = os.path.join(root, "usr/share/containers/systemd/mios-k3s.container")
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            body = fh.read()
    except OSError:
        return None
    code = "\n".join(l for l in body.splitlines() if not l.lstrip().startswith("#"))
    if not re.search(r"\bk3s\s+server\b", code):
        return None
    if "K3S_URL" in code:
        return None          # a join path exists; the peers are not independent
    return ("%d archetypes grant what mios-k3s requires (%s) and the unit runs "
            "`k3s server` with no K3S_URL -- each would stand up its OWN control "
            "plane" % (len(grantors), ", ".join(grantors)))

fs__UNFENCED = re.compile(r"stonith-enabled\s*=\s*false")

def fs_pacemaker_unfenced(data: dict, root: str):
    """Pacemaker configured with fencing off. Detail string, or None."""
    hits = []
    for base in ("usr/lib/systemd/system", "usr/libexec/mios"):
        d = os.path.join(root, base)
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            p = os.path.join(d, name)
            if not os.path.isfile(p):
                continue
            try:
                with open(p, encoding="utf-8", errors="replace") as fh:
                    body = fh.read()
            except OSError:
                continue
            for num, line in enumerate(body.splitlines(), 1):
                if line.lstrip().startswith("#"):
                    continue
                if fs__UNFENCED.search(line):
                    hits.append("%s/%s:%d" % (base, name, num))
    if not hits:
        return None
    return ("fencing is disabled (%s) -- safe on one node, and how split-brain "
            "corrupts data on more" % ", ".join(hits))

fs_DETECTORS = (
    ("k3s-multi-server", fs_k3s_multi_server),
    ("pacemaker-unfenced", fs_pacemaker_unfenced),
)

def fs_detect(data: dict, root: str) -> dict:
    """{hazard-id: detail} for every hazard that currently reproduces."""
    out = {}
    for key, fn in fs_DETECTORS:
        detail = fn(data, root)
        if detail:
            out[key] = detail
    return out

def fs_register(data: dict) -> list:
    reg = ((data.get("blades") or {}).get("hazards") or {}).get("accepted")
    if reg is None:
        return []
    return [str(x).strip() for x in reg if str(x).strip()]

def fs_max_accepted(data: dict):
    val = ((data.get("blades") or {}).get("hazards") or {}).get("max_accepted")
    return val if isinstance(val, int) else None

def fs_violations(data: dict, root: str) -> list:
    viol = []
    shape = fs_fleet_shape(data)
    if not isinstance(shape.get("max_nodes"), int):
        return ["[blades].max_nodes is unset -- the fleet has no declared size, "
                "so nothing can tell a standalone-only config from a broken one"]
    max_nodes = shape["max_nodes"]

    hazards = data.get("blades", {}).get("hazards")
    if hazards is None:
        return ["[blades.hazards] is absent -- nothing bounds how many "
                "above-one-node hazards the tree may carry"]
    if "accepted" not in hazards:
        viol.append("[blades.hazards] declares no `accepted` key -- an implied "
                    "empty register is indistinguishable from a forgotten one")

    reg = fs_register(data)
    if len(reg) != len(set(reg)):
        dupes = sorted({x for x in reg if reg.count(x) > 1})
        viol.append("[blades.hazards].accepted lists a hazard twice: %s"
                    % ", ".join(dupes))
    if reg != sorted(reg):
        viol.append("[blades.hazards].accepted is not sorted -- an unsorted "
                    "register hides an addition inside a reordering")

    known = {k for k, _ in fs_DETECTORS}
    for bad in sorted(set(reg) - known):
        viol.append("[blades.hazards].accepted names '%s', which no detector "
                    "produces -- it can never be retired" % bad)

    found = fs_detect(data, root)
    if max_nodes <= 1:
        # Standalone by declaration: these hazards do not bite. Say so rather
        # than pass silently, because raising max_nodes must re-arm them.
        return viol

    for key in sorted(set(found) - set(reg)):
        viol.append("%s: %s. Fix it, or accept it in [blades.hazards].accepted "
                    "with a justification -- [blades].max_nodes is %d"
                    % (key, found[key], max_nodes))
    for key in sorted(set(reg) & known - set(found)):
        viol.append("[blades.hazards].accepted carries '%s', which no longer "
                    "reproduces -- drop it; the register only shrinks" % key)

    ceiling = fs_max_accepted(data)
    if ceiling is None:
        viol.append("[blades.hazards].max_accepted is unset -- without a ceiling "
                    "the register absorbs new hazards as fast as they appear")
    elif len(reg) > ceiling:
        viol.append("[blades.hazards].accepted holds %d, over the ratchet ceiling "
                    "max_accepted = %d. The ceiling only comes DOWN"
                    % (len(reg), ceiling))
    elif len(reg) < ceiling:
        viol.append("[blades.hazards].accepted holds %d but max_accepted is %d -- "
                    "lower it to %d so the ground gained is held"
                    % (len(reg), ceiling, len(reg)))
    return viol

def fs_main() -> int:
    root = os.environ.get("MIOS_DRIFT_ROOT") or os.environ.get("MIOS_ROOT") or "."
    path = os.path.join(root, fs_TOML)
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except OSError as exc:
        print("check-fleet-safety: cannot read %s: %s" % (path, exc), file=sys.stderr)
        return 1

    viol = fs_violations(data, root)
    if viol:
        for v in viol:
            print("check_fleet_safety: %s" % v, file=sys.stderr)
        return 1

    shape = fs_fleet_shape(data)
    print("[check-fleet-safety] fleet is %s-%s nodes (typical %s); %d "
          "above-one-node hazard(s) accepted (ceiling %s)."
          % (shape.get("min_nodes"), shape.get("max_nodes"),
             shape.get("typical_nodes"), len(fs_register(data)), fs_max_accepted(data)))
    return 0

_GATES = {"toml-integrity": mti_main, "consumer-keys": sck_main, "unit-projection": up_main, "port-fallbacks": pf_main, "ports-bound": pb_main, "variant-registry": vr_main, "deploy-formats": df_main, "role-ssot": rs_main, "node-pool": np_main, "blade-coverage": bc_main, "fleet-safety": fs_main}


def main() -> int:
    # An unknown or missing subcommand must FAIL, never report a clean gate.
    if len(sys.argv) < 2 or sys.argv[1] not in _GATES:
        sys.stderr.write("usage: check-ssot.py {%s}\n" % "|".join(sorted(_GATES)))
        return 2
    return _GATES[sys.argv.pop(1)]()


if __name__ == "__main__":
    sys.exit(main())
