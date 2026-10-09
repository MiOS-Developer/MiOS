#!/usr/bin/env python3
# AI-hint: Runtime and unit gates in one module: container names, privileged Quadlets, service URLs, daemon governor coverage, firstboot degrade-open, firstboot provisioners, artifact verification and resolver twin equivalence. The subcommand selects the gate.
# AI-doc: usr/share/doc/mios/manual/tools.md
# AI-related: tools/verify-images.py, usr/lib/mios/mios_toml.py, usr/lib/mios/userenv.sh
"""Runtime, unit and resolver gates. One module, one subcommand per gate."""
from __future__ import annotations

import sys


"""Gate: every Quadlet declares a ContainerName that matches its unit."""

import glob
import os
import re
import sys

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover -- py<3.11
    import tomli as tomllib  # type: ignore

cn_TOML = "usr/share/mios/mios.toml"
cn_QUADLET_GLOB = "usr/share/containers/systemd/*.container"

def cn_expected_name(unit: str) -> str:
    """A template unit has no single container: it names the instantiated form."""
    if unit.endswith("@"):
        return unit[:-1] + "-%i"
    return unit

def cn_ssot_containers(root: str) -> tuple:
    """({unit: ContainerName}, {unit: enabled}) from the SSOT. A container gated
    off in [quadlets.enable] renders no unit, which is not drift -- but it still
    has to name itself correctly for the day it is switched on."""
    path = os.path.join(root, cn_TOML)
    if not os.path.isfile(path):
        return {}, {}
    with open(path, "rb") as fh:
        data = tomllib.load(fh) or {}
    enabled = (data.get("quadlets") or {}).get("enable") or {}
    out = {}
    for name, block in (data.get("containers") or {}).items():
        if isinstance(block, dict) and isinstance(block.get("Container"), dict):
            out[str(name)] = str(block["Container"].get("ContainerName") or "")
    return out, {k: v is not False for k, v in enabled.items()}

def cn_rendered_containers(root: str) -> dict:
    out = {}
    for g in (cn_QUADLET_GLOB, "usr/share/containers/systemd/users/*.container"):
        for path in sorted(glob.glob(os.path.join(root, g))):
            unit = os.path.basename(path)[: -len(".container")]
            text = open(path, encoding="utf-8", errors="replace").read()
            m = re.search(r"^ContainerName=(.*)$", text, re.M)
            out[unit] = (m.group(1).strip() if m else "")
    return out

def cn_main() -> int:
    root = os.environ.get("MIOS_DRIFT_ROOT", ".")
    ssot, enabled = cn_ssot_containers(root)
    rendered = cn_rendered_containers(root)
    if not ssot:
        print(f"no [containers.*.Container] blocks found under {root}")
        return 1
    if not rendered:
        print(f"no rendered .container files found under {root}")
        return 1

    problems = []
    for unit in sorted(ssot):
        want = cn_expected_name(unit)
        got = ssot[unit]
        if not got:
            problems.append(
                f"{unit}: no ContainerName in the SSOT -- Quadlet would name it "
                f"'systemd-{unit}', which no `systemctl` name matches")
        elif got != want:
            problems.append(f"{unit}: SSOT ContainerName is {got!r}, expected {want!r}")
    for unit in sorted(rendered):
        want = cn_expected_name(unit)
        got = rendered[unit]
        if not got:
            problems.append(f"{unit}.container: rendered unit declares no ContainerName")
        elif got != want:
            problems.append(
                f"{unit}.container: rendered ContainerName is {got!r}, expected {want!r}")
    for unit in sorted(set(ssot) - set(rendered)):
        if enabled.get(unit, True):
            problems.append(
                f"{unit}: enabled in the SSOT but no rendered .container -- regenerate")

    if problems:
        for p in problems:
            print(p)
        return 1
    off = sum(1 for u in ssot if not enabled.get(u, True))
    print(f"every Quadlet names its own container "
          f"(ssot={len(ssot)} rendered={len(rendered)} gated-off={off})")
    return 0

"""Gate: Privileged Quadlets register is minimal, ratcheted, and every entry justified."""

import os
import re
import sys

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None

pq_MIOS_TOML_RELATIVE = "usr/share/mios/mios.toml"

def pq_main() -> int:
    root = os.environ.get("MIOS_DRIFT_ROOT", os.environ.get("MIOS_TOML_ROOT", "."))
    path = os.path.join(root, pq_MIOS_TOML_RELATIVE)
    if not os.path.isfile(path):
        print(f"VIOLATION: {pq_MIOS_TOML_RELATIVE} not found under {root}")
        return 1

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    # Extract [security.privileged_quadlets] block and check lines
    lines = content.splitlines()
    in_block = False
    in_root_array = False

    max_privileged_root = None
    root_entries = []

    for line in lines:
        line_clean = line.strip()
        if line_clean == "[security.privileged_quadlets]":
            in_block = True
            continue
        elif in_block and line_clean.startswith("["):
            in_block = False
            in_root_array = False

        if in_block:
            if line_clean.startswith("max_privileged_root"):
                parts = line_clean.split("=")
                if len(parts) == 2:
                    try:
                        max_privileged_root = int(parts[1].strip())
                    except ValueError:
                        pass
            elif line_clean.startswith("root = ["):
                in_root_array = True
                continue

            if in_root_array:
                if line_clean.startswith("]"):
                    in_root_array = False
                elif line_clean:
                    # e.g., "mios-ceph.container", # comment
                    m = re.search(r'"([^"]+\.container)"\s*,?\s*(#.*)?', line_clean)
                    if m:
                        unit_name = m.group(1)
                        comment = m.group(2)
                        root_entries.append((unit_name, comment))

    problems = []

    if max_privileged_root is None:
        problems.append("VIOLATION: [security.privileged_quadlets].max_privileged_root is not declared")
    else:
        actual_count = len(root_entries)
        if actual_count > max_privileged_root:
            problems.append(
                f"VIOLATION: privileged root count ({actual_count}) exceeds max_privileged_root ceiling ({max_privileged_root})"
            )

    for unit, comment in root_entries:
        if not comment or len(comment.strip("# ").strip()) < 5:
            problems.append(
                f"VIOLATION: privileged Quadlet '{unit}' lacks required capability justification comment"
            )

    if problems:
        for p in problems:
            print(p)
        return 1

    print(
        f"Privileged Quadlets register minimal & justified (entries={len(root_entries)}, max_ceiling={max_privileged_root})"
    )
    return 0

"""Gate: one canonical address per service, or a registered reason there is none."""

import os
import re
import sys

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover -- py<3.11
    import tomli as tomllib  # type: ignore

su_TOML = "usr/share/mios/mios.toml"
# The canonical, path-derived name of [ports].<key>. The retired MIOS_PORT_<KEY>
# spelling is only an input alias now, so a [urls] entry templated with it does
# not count as the port's canonical address.
su__PORT_VAR = re.compile(r"\$\{MIOS_PORTS_([A-Z0-9_]+)\}")

def su_port_keys(data: dict) -> set:
    """Numeric [ports] keys. stack_id is an offset, not a port."""
    ports = (data.get("ports") or {})
    return {k for k, v in ports.items() if isinstance(v, int) and k != "stack_id"}

def su_covered_ports(data: dict) -> set:
    """Port keys templated by at least one [urls] string."""
    out = set()
    for value in (data.get("urls") or {}).values():
        if not isinstance(value, str):
            continue
        for m in su__PORT_VAR.finditer(value):
            out.add(m.group(1).lower())
    return out

def su_register(data: dict) -> list:
    """The shrink-only non-addressable register, in declaration order."""
    reg = (data.get("urls") or {}).get("non_addressable") or []
    return [str(x).strip() for x in reg if str(x).strip()]

def su_classify(data: dict) -> list:
    """Return the violations; empty means every port has exactly one answer."""
    viol = []
    keys = su_port_keys(data)
    if not keys:
        return ["[ports] declares no numeric port -- the gate would pass "
                "vacuously over an empty set"]

    covered = su_covered_ports(data) & keys
    reg = su_register(data)
    reg_set = set(reg)

    if len(reg) != len(reg_set):
        dupes = sorted({k for k in reg if reg.count(k) > 1})
        viol.append("[urls].non_addressable lists a key twice: %s" % ", ".join(dupes))

    for k in sorted(reg_set - keys):
        viol.append("[urls].non_addressable names '%s', which is not a [ports] key "
                    "-- a register entry must name a port that exists" % k)

    for k in sorted(covered & reg_set):
        viol.append("port '%s' has a [urls] entry AND sits in non_addressable -- "
                    "two answers is the drift this gate exists to prevent" % k)

    for k in sorted(keys - covered - reg_set):
        viol.append("port '%s' has no canonical [urls] address and is not in "
                    "[urls].non_addressable -- state how it is addressed" % k)

    return viol

def su_browser_openable(data: dict) -> list:
    """[urls] is what a person clicks, so every value must use a scheme a
    browser opens. A postgresql:// DSN there made the table mean two things."""
    viol = []
    for key, value in sorted((data.get("urls") or {}).items()):
        if not isinstance(value, str):
            continue
        if "://" not in value:
            viol.append("[urls].%s is not a URL: %r" % (key, value))
        elif value.split("://", 1)[0] not in ("http", "https"):
            viol.append("[urls].%s uses the %s scheme -- [urls] is the "
                        "browser-openable surface, so an inter-service address "
                        "belongs on the key its consumers already resolve"
                        % (key, value.split("://", 1)[0]))
    return viol

def su_bare_port_addresses(data: dict) -> list:
    """A localhost URL with a BARE port cannot be offloaded: there is no key for
    an /etc/mios overlay to move, so the address is pinned to this machine."""
    ports = {v: k for k, v in (data.get("ports") or {}).items()
             if isinstance(v, int)}
    url = re.compile(r"(?:https?|ws|postgresql)://(?:localhost|127\.0\.0\.1)[:/]?(\d+)")
    # Rendered unit/container bodies carry ${VAR:-N} defaults by design; the
    # operator-tunable sections are what an overlay has to be able to move.
    skip = ("units.", "containers.", "comment")
    viol = []

    def walk(node, path):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, path + [str(k)])
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, path + ["[%d]" % i])
        elif isinstance(node, str):
            dotted = ".".join(path)
            if any(sk in dotted for sk in skip):
                return
            for m in url.finditer(node):
                num = int(m.group(1))
                if num in ports:
                    viol.append("%s hardcodes :%d instead of "
                                "${MIOS_PORTS_%s} -- an /etc/mios overlay cannot "
                                "move a baked port, so the service can never be "
                                "offloaded" % (dotted, num, ports[num].upper()))

    walk(data, [])
    return viol

def su_main() -> int:
    root = os.environ.get("MIOS_DRIFT_ROOT") or os.environ.get("MIOS_ROOT") or "."
    path = os.path.join(root, su_TOML)
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except OSError as exc:
        print("check-service-urls: cannot read %s: %s" % (path, exc), file=sys.stderr)
        return 1

    viol = su_classify(data) + su_browser_openable(data) + su_bare_port_addresses(data)
    if viol:
        for v in viol:
            print("check_service_urls: %s" % v, file=sys.stderr)
        return 1

    keys, covered, reg = su_port_keys(data), su_covered_ports(data), su_register(data)
    print("[check-service-urls] %d port(s): %d addressed by [urls], %d registered "
          "non-addressable" % (len(keys), len(covered & keys), len(reg)))
    return 0

"""Fail if the daemon governor has a hole: an ungated loop, a dead knob, or a drifted fallback."""
import os
import re
import subprocess
import sys
import tomllib

dg_ROOT = os.environ.get("MIOS_ROOT", ".")
dg_DAEMON = os.path.join(dg_ROOT, "usr/libexec/mios/mios-daemon")
dg_SSOT = os.path.join(dg_ROOT, "usr/share/mios/mios.toml")
dg_CHAT = os.path.join(dg_ROOT, "usr/lib/mios/agent-pipe/mios_pipe/routing/chat.py")

# Loops that serve interactive requests rather than initiating autonomous work;
# gating these would throttle a human, which is the opposite of the intent.
dg_EXEMPT_LOOPS = {"daemon_agent_server_loop"}
# Knobs consumed outside the daemon (agent-pipe owns the budget plane).
dg_ELSEWHERE = {"conversation_token_ceil", "autonomous_token_ceil",
             "autonomous_max_inflight", "window_s"}

def dg_loops_missing_pressure_gate(src: str) -> list:
    lines = src.split("\n")
    starts = [(i, m.group(1)) for i, l in enumerate(lines)
              if (m := re.match(r"^def (\w+_loop)\(", l))]
    missing = []
    for idx, (i, name) in enumerate(starts):
        end = starts[idx + 1][0] if idx + 1 < len(starts) else len(lines)
        if name in dg_EXEMPT_LOOPS:
            continue
        if "_pressure_should_skip" not in "\n".join(lines[i:end]):
            missing.append(name)
    return missing

def dg__consumer_sources(root: str) -> list:
    """Code that may consume a knob: no test_* files, no comment lines -- a knob
    merely NAMED in a docstring or an AI-hint is not a consumer."""
    out = []
    for base in ("usr/libexec/mios", "usr/lib/mios"):
        for dirpath, _, names in os.walk(os.path.join(root, base)):
            if "__pycache__" in dirpath:
                continue
            for n in names:
                if n.startswith("test_") or not (n.endswith(".py") or n.startswith("mios-")):
                    continue
                try:
                    text = open(os.path.join(dirpath, n), encoding="utf-8",
                                errors="replace").read()
                except OSError:
                    continue
                code = "\n".join(l for l in text.split("\n")
                                 if not l.lstrip().startswith("#"))
                out.append(code)
    return out

def dg_dead_knobs(root: str, keys: list) -> list:
    sources = dg__consumer_sources(root)
    dead = []
    for key in keys:
        if key in dg_ELSEWHERE:
            continue
        if not any(f'"{key}"' in s or f"'{key}'" in s for s in sources):
            dead.append(key)
    return dead

def dg_drifted_fallbacks(ssot: dict) -> list:
    if not os.path.isfile(dg_CHAT):
        return []
    text = open(dg_CHAT, encoding="utf-8").read()
    out = []
    for key, want in ssot.get("budget", {}).items():
        m = re.search(rf'"{key}",\s*([0-9_]+)', text)
        if m and int(m.group(1).replace("_", "")) != int(want):
            out.append(f"{key}: fallback {m.group(1)} != SSOT {want}")
    return out

def dg_main() -> int:
    src = open(dg_DAEMON, encoding="utf-8").read()
    data = tomllib.load(open(dg_SSOT, "rb"))
    daemon_keys = [k for k, v in data.get("daemon", {}).items() if not isinstance(v, dict)]
    budget_keys = [k for k, v in data.get("budget", {}).items() if not isinstance(v, dict)]
    bad = []
    bad += [f"autonomous loop without the host-pressure gate: {n}"
            for n in dg_loops_missing_pressure_gate(src)]
    bad += [f"SSOT knob declared but never consumed: [daemon].{k}"
            for k in dg_dead_knobs(dg_ROOT, daemon_keys)]
    bad += [f"agent-pipe budget fallback drifted from the SSOT -- {d}"
            for d in dg_drifted_fallbacks(data)]
    if bad:
        print("\n".join(bad), file=sys.stderr)
        return 1
    total = len(re.findall(r"^def \w+_loop\(", src, re.M))
    print(f"daemon governor complete ({total - len(dg_EXEMPT_LOOPS)} autonomous loops gated, "
          f"{len(daemon_keys)} [daemon] + {len(budget_keys)} [budget] knobs consumed)")
    return 0

"""Gate: no firstboot script aborts on an egress failure (Law 12).

Each egress call reached with errexit active must carry a fallback.
The scoping rules are stated inline beside the patterns below.
"""

import glob
import os
import re
import sys

fdo_EGRESS = re.compile(
    r"\b(curl|wget|podman\s+pull|skopeo\s+copy|dnf\s+(install|upgrade)|"
    r"git\s+clone|bootc\s+(switch|upgrade)|pip\s+install|hf\s+download|"
    r"huggingface-cli\s+download|rpm-ostree|flatpak\s+install)\b")
# errexit does not fire on a condition or on the left of a && list.
fdo_GUARD = re.compile(
    r"\|\||^\s*(if|while|until|elif)\s|&&\s*(true|:|return|exit)|\|\|\s*(return|exit)")
# Column-0 only: an indented 'set +e' is inside a function or subshell
# and must not exempt later top-level lines.
fdo_SETE = re.compile(r"^set\s+-[a-zA-Z]*e|^set\s+-o\s+errexit")
# 'trap ... EXIT' is deliberately NOT an escape: it runs a handler, it does
# not stop errexit aborting an unguarded fetch.
fdo_SETPE = re.compile(r"^set\s+\+[a-zA-Z]*e|^set\s+\+o\s+errexit")
# A fetch named inside a log/echo string is documentation, not a call.
fdo_NARRATION = re.compile(r"^\s*(_?log\w*|echo|printf|cat|#)\b")

fdo_SCAN_GLOBS = ("usr/libexec/mios/*firstboot*", "automation/firstboot/*.sh")
fdo_SKIP_SUFFIXES = (".pyc", ".bak", ".keep", ".orig", ".rej")


def fdo_logical_lines(lines):
    """Join continuations and parenthesised runs into one logical line each.

    The guard usually lands after a continuation or a closing paren, so a
    physical scan reports guarded calls as unguarded.
    """
    out, buf, start, depth = [], "", None, 0
    for num, line in enumerate(lines, 1):
        if start is None:
            start = num
        stripped = line.rstrip()
        buf += stripped[:-1] if stripped.endswith("\\") else line
        depth = max(0, depth + line.count("(") - line.count(")"))
        if stripped.endswith("\\") or depth > 0:
            buf += " "
            continue
        out.append((start, buf))
        buf, start = "", None
    if buf:
        out.append((start or len(lines), buf))
    return out


def fdo_scan(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            lines = handle.read().split("\n")
    except OSError as exc:
        return [(0, "unreadable: %s" % exc)]
    errexit, bad = False, []
    for num, line in fdo_logical_lines(lines):
        if line.lstrip().startswith("#") or fdo_NARRATION.search(line):
            continue
        if fdo_SETE.search(line):
            errexit = True
        if fdo_SETPE.search(line):
            errexit = False
        if errexit and fdo_EGRESS.search(line) and not fdo_GUARD.search(line):
            bad.append((num, " ".join(line.split())[:100]))
    return bad


def fdo_main() -> int:
    root = os.environ.get("MIOS_DRIFT_ROOT") or os.getcwd()
    files = []
    for pattern in fdo_SCAN_GLOBS:
        files.extend(glob.glob(os.path.join(root, pattern)))
    files = sorted(f for f in files
                   if os.path.isfile(f) and not f.endswith(fdo_SKIP_SUFFIXES))

    if not files:
        # An empty scan set is never a pass: the globs are the gate's subject.
        print("no firstboot scripts matched %s -- the gate has no subject"
              % ", ".join(fdo_SCAN_GLOBS))
        return 1

    findings = []
    for path in files:
        rel = os.path.relpath(path, root).replace(os.sep, "/")
        for num, text in fdo_scan(path):
            findings.append(
                "%s:%d does not degrade open (Law 12): egress call runs under "
                "active set -e with no fallback -- %s" % (rel, num, text))

    if findings:
        for line in findings:
            print(line)
        return 1

    print("%d firstboot script(s) scanned; every egress call degrades open"
          % len(files))
    return 0

"""Gate: every first-boot provisioner triple (fetcher + unit + preset) is whole."""

import os
import re
import sys

# unit basename -> (libexec fetcher, /var dirs the fetcher writes into)
fp_PROVISIONERS = {
    "mios-models-firstboot.service": (
        "usr/libexec/mios/mios-models-firstboot",
        ("/var/lib/mios/llamacpp/models",),
    ),
}

fp_UNIT_DIR = "usr/lib/systemd/system"
fp_PRESET = "usr/lib/systemd/system-preset/90-mios.preset"
fp_TMPFILES_DIR = "usr/lib/tmpfiles.d"

def fp_tmpfiles_dirs(root: str) -> set:
    """Every directory path declared by a tmpfiles.d d/D/v/f line."""
    out = set()
    d = os.path.join(root, fp_TMPFILES_DIR)
    if not os.path.isdir(d):
        return out
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".conf"):
            continue
        with open(os.path.join(d, fn), encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                if len(parts) >= 2 and parts[0] in ("d", "D", "v", "f", "F"):
                    out.add(parts[1])
    return out

def fp_unit_field(text: str, key: str):
    m = re.search(r"^%s\s*=\s*(.*)$" % re.escape(key), text, re.M)
    return m.group(1).strip() if m else None

def fp_check_one(root, unit_name, fetcher_rel, var_dirs, declared):
    bad = []
    unit_path = os.path.join(root, fp_UNIT_DIR, unit_name)
    fetcher_path = os.path.join(root, fetcher_rel)

    if not os.path.isfile(fetcher_path):
        bad.append(f"{unit_name}: fetcher {fetcher_rel} does not exist")
        return bad
    if not os.path.isfile(unit_path):
        bad.append(f"{unit_name}: unit file missing from {fp_UNIT_DIR}/")
        return bad

    unit = open(unit_path, encoding="utf-8", errors="replace").read()
    fetcher = open(fetcher_path, encoding="utf-8", errors="replace").read()

    execstart = fp_unit_field(unit, "ExecStart") or ""
    if "/" + fetcher_rel.split("usr/", 1)[-1] not in execstart.replace("/usr/", "/"):
        if os.path.basename(fetcher_rel) not in execstart:
            bad.append(f"{unit_name}: ExecStart does not run {fetcher_rel} "
                       f"(got {execstart!r})")

    cond = fp_unit_field(unit, "ConditionPathExists") or ""
    if not cond.startswith("!"):
        bad.append(f"{unit_name}: no ConditionPathExists=!<sentinel> gate "
                   f"(got {cond!r}) -- the oneshot would re-run every boot")
    else:
        sentinel = cond[1:].strip()
        if sentinel not in fetcher:
            bad.append(f"{unit_name}: gates on {sentinel} but the fetcher never "
                       f"names that path -- the sentinel is never written, so the "
                       f"unit runs forever")

    preset_path = os.path.join(root, fp_PRESET)
    if os.path.isfile(preset_path):
        preset = open(preset_path, encoding="utf-8", errors="replace").read()
        if not re.search(r"^enable\s+%s\s*$" % re.escape(unit_name), preset, re.M):
            bad.append(f"{unit_name}: not enabled in {fp_PRESET} -- installed but "
                       f"never started")
    else:
        bad.append(f"{fp_PRESET} is missing")

    for d in var_dirs:
        if d not in declared:
            bad.append(f"{unit_name}: writes {d}, which no tmpfiles.d file "
                       f"declares (Architectural Law 2: no mkdir in /var)")
    return bad

def fp_main() -> int:
    root = os.environ.get("MIOS_DRIFT_ROOT") or os.environ.get("MIOS_ROOT") or "."
    declared = fp_tmpfiles_dirs(root)
    bad = []
    for unit_name, (fetcher_rel, var_dirs) in sorted(fp_PROVISIONERS.items()):
        bad += fp_check_one(root, unit_name, fetcher_rel, var_dirs, declared)
    if bad:
        for line in bad:
            print(line)
        return 1
    print(f"first-boot provisioner triples are whole "
          f"(checked={len(fp_PROVISIONERS)} fetcher+unit+preset+tmpfiles)")
    return 0

"""Prove the artifact gate can fail.

`publish` depends on `verify-images` to establish that the artifacts it is
about to push are real. The version that shipped ended on a failure counter
that stays zero when the glob loop matches nothing, so an empty build tree
passed it. A gate that cannot fail is worth less than no gate, because the
pipeline is built as though it were checking something.

So this drives the real verifier three ways -- an empty tree, a complete set of
fixtures, and the same set with one artifact removed -- and fails unless the
verdicts come back reject, accept, reject-naming-the-missing-format. It also
holds the wiring in place: the recipe must delegate here, `publish` must depend
on it, and every format the `all` target builds must declare where its output
lands.
"""
import gzip
import io
import os
import re
import subprocess
import sys
import tarfile
import tempfile

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    import tomli as tomllib  # type: ignore

# One valid-enough artifact per format: the right leading bytes and enough of
# them to clear the size floor. Written as (relative path, builder key).
vi_FIXTURES = {
    "oci-archive":   ("oci-archive/mios-test.tar", "tar"),
    "raw":           ("raw/image/disk.raw", "raw"),
    "iso":           ("iso/bootiso/install.iso", "iso"),
    "usb-installer": ("usb-installer/install-usb.iso", "iso"),
    "qcow2":         ("qcow2/qcow2/disk.qcow2", "qcow2"),
    "vhdx":          ("vhdx/disk.vhdx", "vhdx"),
    "wsl2":          ("wsl2/mios-rootfs.tar.gz", "targz"),
}

def vi__write(path, blob):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(blob)

def vi__padded(size, *placed):
    """A buffer of `size` bytes with (offset, bytes) written into it."""
    buf = bytearray(b"\x00" * size)
    for offset, blob in placed:
        buf[offset:offset + len(blob)] = blob
    return bytes(buf)

def vi__build(kind, size):
    if kind == "iso":
        return vi__padded(size, (32769, b"CD001"))
    if kind == "qcow2":
        return vi__padded(size, (0, b"QFI\xfb"))
    if kind == "vhdx":
        return vi__padded(size, (0, b"vhdxfile"))
    if kind == "raw":
        return vi__padded(size, (510, b"\x55\xaa"), (512, b"EFI PART"))
    if kind in ("tar", "targz"):
        raw = io.BytesIO()
        with tarfile.open(fileobj=raw, mode="w") as tf:
            member = tarfile.TarInfo("rootfs/payload.bin")
            member.size = size
            tf.addfile(member, io.BytesIO(os.urandom(size)))
        if kind == "tar":
            return raw.getvalue()
        return gzip.compress(raw.getvalue(), 1)
    raise AssertionError(kind)

def vi_make_tree(outdir, size, skip=()):
    for name, (rel, kind) in sorted(vi_FIXTURES.items()):
        if name in skip:
            continue
        vi__write(os.path.join(outdir, *rel.split("/")), vi__build(kind, size))

def vi_run_verifier(root, outdir):
    proc = subprocess.run(
        [sys.executable, os.path.join(root, "tools", "verify-images.py"),
         "--root", root, "--output-dir", outdir],
        capture_output=True, text=True)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")

def vi_structural(root, ssot, viol):
    jpath = os.path.join(root, "Justfile")
    if not os.path.isfile(jpath):
        viol.append("Justfile is missing, so nothing verifies anything")
        return
    with open(jpath, encoding="utf-8", errors="replace") as fh:
        just = fh.read()

    recipe = re.search(r"^verify-images:\n((?:[ \t]+[^\n]*\n|\n)*)", just, re.M)
    if not recipe:
        viol.append("the Justfile defines no verify-images recipe")
    elif "tools/verify-images.py" not in recipe.group(1):
        viol.append("the verify-images recipe no longer runs"
                    " tools/verify-images.py -- an inline glob loop is how this"
                    " gate came to pass over an empty tree")

    pub = re.search(r"^publish:([^\n]*)", just, re.M)
    if not pub:
        viol.append("the Justfile defines no publish recipe")
    elif "verify-images" not in pub.group(1).split():
        viol.append("publish no longer depends on verify-images, so the push is"
                    " guarded by nothing")

    formats = (ssot.get("deploy") or {}).get("formats") or {}
    by_target = {s.get("target"): n for n, s in formats.items()
                 if isinstance(s, dict)}
    built = re.search(r"^all:([^\n]*)", just, re.M)
    for target in (built.group(1).split() if built else []):
        if target == "build":
            continue
        name = by_target.get(target)
        if name is None:
            viol.append("the all target builds %r, which no [deploy.formats]"
                        " entry claims" % target)
            continue
        if not formats[name].get("artifacts"):
            viol.append("[deploy.formats.%s] declares no artifacts globs, so the"
                        " format the all target builds is one the verifier does"
                        " not require" % name)

def vi_behavioural(root, ssot, viol):
    floor = int(((ssot.get("deploy") or {})
                 .get("verify") or {}).get("min_bytes", 1048576))
    size = floor + 4096

    with tempfile.TemporaryDirectory(prefix="mios-verify-images-") as tmp:
        empty = os.path.join(tmp, "empty")
        os.makedirs(empty)
        rc, out = vi_run_verifier(root, empty)
        if rc == 0:
            viol.append("verify-images returned success over an empty build"
                        " tree -- this is the defect the gate exists to catch")
        for name in vi_FIXTURES:
            if name not in out:
                viol.append("verify-images did not name the missing format %r"
                            " when nothing was built" % name)

        full = os.path.join(tmp, "full")
        vi_make_tree(full, size)
        rc, out = vi_run_verifier(root, full)
        if rc != 0:
            viol.append("verify-images rejected a complete set of valid"
                        " artifacts (exit %d):\n%s" % (rc, out.strip()))

        for name in sorted(vi_FIXTURES):
            rel = vi_FIXTURES[name][0]
            path = os.path.join(full, *rel.split("/"))
            with open(path, "rb") as fh:
                blob = fh.read()
            os.remove(path)
            rc, out = vi_run_verifier(root, full)
            if rc == 0:
                viol.append("verify-images passed with the %s artifact deleted"
                            % name)
            elif name not in out:
                viol.append("verify-images failed with the %s artifact deleted"
                            " but did not name it" % name)
            vi__write(path, blob)

        corrupt = os.path.join(full, *vi_FIXTURES["qcow2"][0].split("/"))
        with open(corrupt, "rb") as fh:
            good = fh.read()
        vi__write(corrupt, b"\x00" * size)
        rc, _ = vi_run_verifier(root, full)
        if rc == 0:
            viol.append("verify-images passed a %d-byte run of zeroes named as a"
                        " qcow2 -- the header it prints is being compared"
                        " against nothing" % size)
        vi__write(corrupt, good)

def vi_main():
    root = os.environ.get("MIOS_DRIFT_ROOT") or os.environ.get("MIOS_ROOT") or os.getcwd()
    with open(os.path.join(root, "usr/share/mios/mios.toml"), "rb") as fh:
        ssot = tomllib.load(fh)

    viol = []
    vi_structural(root, ssot, viol)
    if not os.path.isfile(os.path.join(root, "tools", "verify-images.py")):
        viol.append("tools/verify-images.py is absent, so the publish gate has"
                    " no implementation")
    else:
        vi_behavioural(root, ssot, viol)

    print("\n".join(viol))
    if viol:
        return 1
    print("[check-verify-images] an empty tree, a missing format and a corrupt"
          " artifact are each rejected by name", file=sys.stderr)
    return 0


import os
import sys
import re
import json
import subprocess
import shlex

def rt_main():
    root = os.environ.get("MIOS_DRIFT_ROOT")
    if not root:
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

    os.environ["MIOS_VENDOR_TOML"] = os.path.join(root, "usr/share/mios/mios.toml").replace('\\', '/')
    os.environ["MIOS_HOST_TOML"] = os.path.join(root, "etc/mios/mios.toml").replace('\\', '/')
    os.environ["MIOS_USER_TOML"] = os.path.join(root, "nonexistent.toml").replace('\\', '/')
    os.environ["MIOS_VENDOR_TOML_D"] = os.path.join(root, "usr/lib/mios/mios.d").replace('\\', '/')
    os.environ["MIOS_HOST_TOML_D"] = os.path.join(root, "etc/mios/mios.d").replace('\\', '/')
    os.environ["MIOS_USER_TOML_D"] = os.path.join(root, "nonexistent_d").replace('\\', '/')

    lib_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../usr/lib/mios"))
    sys.path.insert(0, lib_path)
    if root:
        alt_path = os.path.join(root, "usr/lib/mios")
        if os.name == "nt" and alt_path.startswith("/mnt/c/"):
            alt_path = "C:/" + alt_path[7:]
        sys.path.insert(0, alt_path)
    try:
        import mios_toml
    except ImportError as e:
        print(f"Error: Could not import mios_toml: {e}", file=sys.stderr)
        sys.exit(1)

    env = os.environ.copy()

    _TIER_VARS = {
        "MIOS_ROOT", "MIOS_TOML", "MIOS_TOML_ROOT",
        "MIOS_VENDOR_TOML", "MIOS_VENDOR_TOML_D",
        "MIOS_HOST_TOML", "MIOS_HOST_TOML_D",
        "MIOS_USER_TOML", "MIOS_USER_TOML_D", "MIOS_PYTHON_BIN",
    }
    for _k in [k for k in env if k.startswith("MIOS_") and k not in _TIER_VARS]:
        env.pop(_k, None)

    env["MSYS_NO_PATHCONV"] = "1"
    env["PYTHONPATH"] = os.path.join(root, "usr/lib/mios").replace('\\', '/') + (os.pathsep + env["PYTHONPATH"] if "PYTHONPATH" in env else "")
    env.pop("MIOS_TOML_RESOLVED", None)

    bash_exe = "bash"
    if os.name == "nt":
        for path in [r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe"]:
            if os.path.exists(path):
                bash_exe = path
                break

    py_exec = sys.executable.replace('\\', '/')
    if os.name == "nt" and py_exec[1:2] == ":":
        py_exec_msys = "/" + py_exec[0].lower() + py_exec[2:]
    else:
        py_exec_msys = py_exec
    env["MIOS_PYTHON_BIN"] = py_exec_msys

    userenv_script = os.path.join(root, 'usr/lib/mios/userenv.sh').replace('\\', '/')
    py_exec = sys.executable.replace('\\', '/')
    dump = f"source {shlex.quote(userenv_script)} && {shlex.quote(py_exec)} -c \"import os, json; print(json.dumps({{k: v for k, v in os.environ.items() if k.startswith('MIOS_')}}))\""

    def bash_exports(tier_env):
        cmd = [bash_exe, "-c", dump]
        try:
            out = subprocess.check_output(cmd, env=tier_env, stderr=subprocess.STDOUT).decode("utf-8")
            print(f"BASH OUTPUT: {out}", file=sys.stderr)
            got = json.loads(out)
            got.pop("MIOS_PYTHON_BIN", None)
            return got
        except subprocess.CalledProcessError as e:
            print("Error: userenv.sh execution failed:\n", e.output.decode("utf-8", errors="ignore"), file=sys.stderr)
            sys.exit(1)
        except json.JSONDecodeError as e:
            print(f"Error: Failed to parse env JSON: {e}\nOutput was:\n{out}", file=sys.stderr)
            sys.exit(1)

    # The reference is pure Python: with mios-resolver on PATH, mios_toml loads
    # its merged tree from the binary, and the gate would partly compare Rust
    # with itself.
    os.environ["MIOS_RESOLVER_NATIVE"] = "0"
    mios_toml.clear_cache()
    exports_map = mios_toml.emit_exports()
    pure_exports = dict(exports_map)

    ref_path = os.path.join(root, "usr/share/mios/referenced_names.txt")
    if os.path.isfile(ref_path):
        try:
            with open(ref_path, "r", encoding="utf-8") as f:
                for line in f:
                    v = line.strip()
                    if v and v not in exports_map:
                        exports_map[v] = ""
        except Exception:
            pass

    toml_vars = exports_map

    loopback = mios_toml.get("pgvector", "listen_loopback")
    if loopback is None:
        loopback = True
    toml_vars["MIOS_PG_BIND_ADDR"] = "127.0.0.1" if loopback else "0.0.0.0"

    ignore_vars = {
        "MIOS_VENDOR_TOML", "MIOS_HOST_TOML", "MIOS_USER_TOML",
        "MIOS_VENDOR_TOML_D", "MIOS_HOST_TOML_D", "MIOS_USER_TOML_D",
        "MIOS_DRIFT_ROOT", "MIOS_DRIFT_CHECK_ROOT", "MIOS_DRIFT_CHECK_SOFT",
        "MIOS_TOML_ROOT", "MIOS_ROOT_LIB", "MIOS_CONFIG_DIR", "MIOS_ROOT"
    }

    # userenv.sh resolves through the first tier it finds: mios-resolver, then
    # miosd, then mios_toml.py. Whatever this PATH offers is one run; each
    # native resolver built in this tree is forced first in a run of its own,
    # so CI (which has neither installed) still compares Rust with Python.
    exe = ".exe" if os.name == "nt" else ""
    tiers = [("default", env, None)]
    for label, rel, skip_tier1 in (("mios-resolver", "tools/native/target/debug/mios-resolver", False),
                                   ("miosd", "src/mios-rs/target/debug/miosd", True)):
        binary = os.path.join(root, rel + exe)
        if not os.path.isfile(binary):
            continue
        tier_env = dict(env)
        if skip_tier1:
            tier_env["MIOS_MIGRATION_USE_RUST_RESOLVER_SHELL"] = "false"
        tiers.append((label, tier_env, binary))

    import tempfile
    mismatches = []
    for label, tier_env, binary in tiers:
        with tempfile.TemporaryDirectory(prefix="mios-twin-") as d:
            if binary:
                try:
                    os.symlink(binary, os.path.join(d, os.path.basename(binary)))
                except OSError as e:
                    print(f"  [resolver-twin] {label}: cannot stage {binary} ({e}); tier not compared", file=sys.stderr)
                    continue
                tier_env = dict(tier_env, PATH=d + os.pathsep + tier_env.get("PATH", ""))
            bash_vars = bash_exports(tier_env)
        for k, expected in sorted(toml_vars.items()):
            if k in ignore_vars:
                continue
            actual = bash_vars.get(k)
            if actual != expected:
                if expected == "" and (actual is None or actual == ""):
                    continue
                mismatches.append(f"[{label}] Var {k}: Toml resolved {expected!r}, Bash resolved {actual!r}")
        for k, actual in sorted(bash_vars.items()):
            if k in ignore_vars:
                continue
            if k not in toml_vars:
                mismatches.append(f"[{label}] Unexpected Var {k}: Bash resolved {actual!r}, Toml has no entry")

    # mios_toml.py itself loads mios-resolver's merged tree when the binary is
    # on PATH; its exports must not change with where the tree came from.
    native = next((b for t, _, b in tiers if t == "mios-resolver"), None)
    if native:
        saved_path = os.environ.get("PATH", "")
        with tempfile.TemporaryDirectory(prefix="mios-twin-") as d:
            try:
                os.symlink(native, os.path.join(d, os.path.basename(native)))
                os.environ["PATH"] = d + os.pathsep + saved_path
                os.environ.pop("MIOS_RESOLVER_NATIVE", None)
                mios_toml.clear_cache()
                on_native = mios_toml.emit_exports()
            finally:
                os.environ["PATH"] = saved_path
                os.environ["MIOS_RESOLVER_NATIVE"] = "0"
                mios_toml.clear_cache()
        tiers.append(("mios_toml.py on mios-resolver's tree", None, native))
        for k in sorted(set(pure_exports) | set(on_native)):
            if k not in ignore_vars and pure_exports.get(k) != on_native.get(k):
                mismatches.append(f"[mios_toml.py on mios-resolver's tree] Var {k}: pure Python {pure_exports.get(k)!r}, on the native tree {on_native.get(k)!r}")

    if mismatches:
        for m in mismatches:
            print(f"  [resolver-twin] {m}", file=sys.stderr)
        sys.exit(1)

    print(f"SUCCESS: resolvers are equivalent ({', '.join(t[0] for t in tiers)})!")
    sys.exit(0)

_GATES = {"container-names": cn_main, "privileged-quadlets": pq_main, "service-urls": su_main, "daemon-governor": dg_main, "firstboot-degrade-open": fdo_main, "firstboot-provisioners": fp_main, "verify-images": vi_main, "resolver-twin": rt_main}


def main() -> int:
    # An unknown or missing subcommand must FAIL, never report a clean gate.
    if len(sys.argv) < 2 or sys.argv[1] not in _GATES:
        sys.stderr.write("usage: check-runtime.py {%s}\n" % "|".join(sorted(_GATES)))
        return 2
    return _GATES[sys.argv.pop(1)]()


if __name__ == "__main__":
    sys.exit(main())
