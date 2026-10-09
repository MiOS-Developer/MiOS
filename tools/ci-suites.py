#!/usr/bin/env python3
# AI-hint: Resolves the [ci] suite registry for the runners and fails when a tracked suite is neither registered in a tier nor exempted.
# AI-related: usr/share/mios/mios.toml, tests/run-suites.sh, automation/98-drift-checks.sh
import fnmatch
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mios_tracked import tracked, GitUnavailable  # noqa: E402

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

# Scanned for coverage. Anything a runner could reasonably be expected to
# execute, so a dead suite has to be declared dead rather than merely ignored.
#
# The two fitness-function stages are named because leaving them out was not a
# gap, it was a hole: deleting "automation/98-drift-checks.sh" from
# [ci.tiers].gate raised no violation here, the gate tier stayed non-empty so
# run-suites.sh's zero-suite guard never fired either, and the entire drift gate
# stopped running while both reported green.
TRACKED = ("tests/test-*.sh", "tests/test-*.py", "tests/*.sh", "tests/**/*.sh",
           "automation/lint-*.sh", "automation/97-*.sh", "automation/98-*.sh")
# Known still outside TRACKED, so still able to fall out of CI unnoticed:
# automation/test_*.sh, automation/tests/*.sh, automation/lib/test_*.sh,
# usr/lib/mios/**, usr/libexec/mios/test_*.py and usr/share/mios/tests/*.sh --
# 19 tracked suites at last count. Widening to them is a registry change, not a
# reader change: each has to land in a tier or in [ci.exempt] first.

def _root() -> str:
    return os.environ.get("MIOS_DRIFT_ROOT") or os.environ.get("MIOS_ROOT") or os.getcwd()

def _load(root: str) -> dict:
    with open(os.path.join(root, "usr/share/mios/mios.toml"), "rb") as fh:
        return tomllib.load(fh).get("ci") or {}

def _load_packages(root: str) -> dict:
    with open(os.path.join(root, "usr/share/mios/mios.toml"), "rb") as fh:
        return tomllib.load(fh).get("packages") or {}

def _tokens(value, name: str) -> list:
    if not isinstance(value, list) or any(
            not isinstance(v, str) or not v or v.startswith("-")
            or any(c.isspace() for c in v) for v in value):
        raise ValueError(f"{name} must be a list of non-empty argument tokens")
    return value


def fedora_arguments(root: str, ci: dict, option: str) -> list:
    """Resolve CI provisioning from the vendor package dependency closure."""
    fed = ci.get("fedora")
    if not isinstance(fed, dict):
        raise ValueError("mios.toml has no [ci.fedora] table")
    if option == "--fedora-image":
        image = fed.get("image")
        return _tokens([image], "[ci.fedora].image")
    if option == "--dnf-repos":
        return list(dict.fromkeys(_tokens(fed.get("repos"), "[ci.fedora].repos")))
    packages = _load_packages(root)
    result, visited = [], set()

    def visit(name: str, trail: tuple = ()) -> None:
        if name in trail:
            raise ValueError("cyclic package section dependency: " + " -> ".join((*trail, name)))
        if name in visited:
            return
        section = packages.get(name)
        if not isinstance(section, dict):
            raise ValueError(f"[ci.fedora] names missing [packages.{name}]")
        if section.get("enable", True) is not True:
            raise ValueError(f"[ci.fedora] requires disabled [packages.{name}]")
        pkgs = _tokens(section.get("pkgs"), f"[packages.{name}].pkgs")
        if not pkgs:
            raise ValueError(f"[packages.{name}].pkgs is empty")
        for dep in _tokens(section.get("requires_sections", []),
                           f"[packages.{name}].requires_sections"):
            visit(dep, (*trail, name))
        result.extend(pkgs)
        visited.add(name)

    for name in _tokens(fed.get("package_sets"), "[ci.fedora].package_sets"):
        visit(name)
    result.extend(_tokens(fed.get("packages"), "[ci.fedora].packages"))
    if not result:
        raise ValueError("[ci.fedora] resolves no DNF packages")
    return list(dict.fromkeys(result))


def _tracked(root: str) -> list:
    """git-tracked, not os.walk: a runner executes what the repository ships.

    Raises GitUnavailable rather than returning an empty corpus.
    """
    return sorted(set(tracked(root, *TRACKED)))

def _glob_members(root: str, spec: dict) -> list:
    d = spec.get("dir", "")
    pat = spec.get("glob", "*")
    skip = set(spec.get("skip") or ())
    full = os.path.join(root, d)
    if not os.path.isdir(full):
        return []
    # fnmatchcase, never fnmatch: the case-insensitive form resolves the
    # registry differently on Windows than it does on the runner.
    return [f"{d}/{fn}" for fn in sorted(os.listdir(full))
            if fnmatch.fnmatchcase(fn, pat) and fn not in skip]

def _registered(root: str, ci: dict) -> dict:
    """path -> tier, for every listed suite and every glob member."""
    reg = {}
    for tier, paths in (ci.get("tiers") or {}).items():
        for p in paths:
            reg[p] = tier
    for spec in (ci.get("globs") or {}).values():
        for p in _glob_members(root, spec):
            reg[p] = spec.get("tier", "unit")
    return reg

# `if:` values that switch a step off outright. An arbitrary expression is left
# alone -- guessing at one would be a worse lie than reading none.
_DISABLED = {"false", "'false'", '"false"', "${{ false }}", "${{false}}"}
_INVOKE = re.compile(r"run-suites\.sh\s+(\S+)")

def _live_run_commands(body: str) -> list:
    """The shell commands a workflow actually executes.

    Parity used to be `f"run-suites.sh {tier}" in body`: a raw substring over the
    whole file, comments and disabled steps included. A step commented out, or
    guarded `if: false`, kept satisfying the one check whose entire job is to
    notice that a publisher has quietly stopped running a tier. Only the value of
    a live `run:` key counts now.
    """
    steps, cur = [], None
    for raw in body.splitlines():
        if not raw.strip():
            continue
        ind = len(raw) - len(raw.lstrip())
        if raw.lstrip().startswith("-"):
            if cur:
                steps.append(cur)
            cur = [ind, [raw]]
        elif cur and ind > cur[0]:
            cur[1].append(raw)
        elif cur:
            steps.append(cur)
            cur = None
    if cur:
        steps.append(cur)

    cmds = []
    for _, chunk in steps:
        keys = [ln.strip()[2:].lstrip() if ln.strip().startswith("- ")
                else ln.strip() for ln in chunk]
        if any(k.startswith("if:") and k[3:].strip() in _DISABLED for k in keys):
            continue
        block = 0
        for raw, key in zip(chunk, keys):
            ind = len(raw) - len(raw.lstrip())
            if block:
                if ind >= block:
                    line = raw.strip()
                    if not line.startswith("#"):
                        cmds.append(line.split(" #", 1)[0].strip())
                    continue
                block = 0
            if key.startswith("#"):
                continue
            if key.startswith("run:"):
                val = key[4:].strip()
                if val.startswith(("|", ">")):
                    block = ind + 1
                else:
                    cmds.append(val.split(" #", 1)[0].strip())
    return cmds

def cmd_list(root: str, ci: dict, tier: str) -> int:
    reg = _registered(root, ci)
    if tier not in (ci.get("tiers") or {}) and tier not in {
            s.get("tier") for s in (ci.get("globs") or {}).values()}:
        print(f"unknown tier: {tier}", file=sys.stderr)
        return 2
    for path in sorted(p for p, t in reg.items() if t == tier):
        runner = "python3" if path.endswith(".py") else "bash"
        print(f"{runner}\t{path}")
    return 0

def cmd_check(root: str, ci: dict) -> int:
    viol = []
    reg = _registered(root, ci)
    exempt = ci.get("exempt") or {}

    for path, tier in sorted(reg.items()):
        if not os.path.isfile(os.path.join(root, path)):
            viol.append(f"[ci.tiers].{tier} lists {path}, which does not exist")

    listed = {}
    for tier, paths in (ci.get("tiers") or {}).items():
        for p in paths:
            if p in listed:
                viol.append(f"{p} is registered in both {listed[p]} and {tier}")
            listed[p] = tier

    for name, spec in sorted((ci.get("globs") or {}).items()):
        d, pat = spec.get("dir", ""), spec.get("glob", "*")
        if not os.path.isdir(os.path.join(root, d)):
            viol.append(f"[ci.globs.{name}] dir '{d}' is not a directory -- a"
                        " renamed dir registers zero suites and says nothing")
        elif not _glob_members(root, spec):
            viol.append(f"[ci.globs.{name}] '{d}/{pat}' matches no file -- an"
                        " empty glob removes every suite it used to supply"
                        " without moving a count anything watches")
        if spec.get("skip") and not str(spec.get("skip_reason") or "").strip():
            viol.append(f"[ci.globs.{name}] skips {len(spec['skip'])} suite(s)"
                        " with no skip_reason -- a skip is an exemption")
        for fn in spec.get("skip") or ():
            if not (fnmatch.fnmatchcase(fn, pat)
                    and os.path.isfile(os.path.join(root, d, fn))):
                viol.append(f"[ci.globs.{name}] skip '{fn}' names no {d}/{pat}"
                            " file -- a stale skip would silently exempt the"
                            " next suite given that name")

    # A runner is exempt from the tiers because it EXECUTES them. One that never
    # reads the registry is not a harness, it is a suite parked out of reach of
    # both the tiers and the exemption ratchet.
    runners = set(ci.get("runners") or ())
    for path in sorted(runners):
        full = os.path.join(root, path)
        if not os.path.isfile(full):
            viol.append(f"[ci].runners lists {path}, which does not exist")
        elif "ci-suites.py" not in Path(full).read_text(encoding="utf-8", errors="replace"):
            viol.append(f"[ci].runners {path} never reads the suite registry, so"
                        " it is not a harness -- a suite listed here runs nowhere"
                        " and never touches [ci].max_exempt_suites")

    try:
        suites = _tracked(root)
    except GitUnavailable as exc:
        suites = []
        viol.append(f"cannot enumerate tracked suites: {exc}")
    for path in suites:
        if path in reg or path in exempt or path in runners:
            continue
        viol.append(f"{path} is tracked but runs in no tier and is not exempt")

    for path, reason in sorted(exempt.items()):
        if not str(reason).strip():
            viol.append(f"[ci.exempt] {path} carries no reason")
        if path in reg:
            viol.append(f"{path} is both exempt and registered in {reg[path]}")

    tiers = sorted(ci.get("tiers") or {})
    for wf in (".github/workflows/mios-ci.yml", ".forgejo/workflows/build-mios.yml"):
        full = os.path.join(root, wf)
        if not os.path.isfile(full):
            viol.append(f"{wf} is missing -- both publishers must run the tiers")
            continue
        body = Path(full).read_text(encoding="utf-8", errors="replace")
        cmds = _live_run_commands(body)
        if not cmds:
            viol.append(f"{wf} has no live 'run:' step at all -- parity is read"
                        " off executed commands, not off the file's text")
        ran = {m.group(1).strip("'\"") for c in cmds for m in _INVOKE.finditer(c)}
        for tier in tiers:
            if tier not in ran:
                viol.append(f"{wf} never runs the '{tier}' tier")

    skips = ci.get("tool_skips") or {}
    for path, reason in sorted(skips.items()):
        if path not in reg:
            viol.append(f"[ci.tool_skips] {path} runs in no tier -- only a registered suite may skip")
        if not str(reason).strip():
            viol.append(f"[ci.tool_skips] {path} names no missing tool")
    if len(skips) > int(ci.get("max_tool_skips") or 0):
        viol.append(f"tool-skipping suites {len(skips)} > ceiling {ci.get('max_tool_skips') or 0}")

    # Restore the provisioning and image contracts alongside suite coverage.
    # A registered tier cannot run when the runner's dependency exporter broke.
    for option in ("--dnf-repos", "--dnf-packages", "--fedora-image"):
        try:
            fedora_arguments(root, ci, option)
        except (OSError, ValueError, TypeError) as exc:
            viol.append(f"{option}: {exc}")
    fedora = ci.get("fedora")
    want = fedora.get("image") if isinstance(fedora, dict) else None
    if want:
        wf = os.path.join(root, ".github/workflows/mios-ci.yml")
        body = Path(wf).read_text(encoding="utf-8") if os.path.isfile(wf) else ""
        job = re.search(r"^  drift-gate:\n(.*?)(?=^  \S|\Z)", body, re.M | re.S)
        got = re.search(r"^    container:\s*\n\s+image:\s*(\S+)",
                        job.group(1), re.M) if job else None
        if not got or got.group(1).strip("'\"") != want:
            viol.append("drift-gate container differs from [ci.fedora].image")
    # main() reads [ci] from this same file, so on the real gate path it exists;
    # only a unit fixture handing cmd_check a bare [ci] dict lacks it.
    if os.path.isfile(os.path.join(root, "usr/share/mios/mios.toml")):
        viol.extend(devcontainer_check(root))

    if suite_timeout(ci) is None:
        viol.append("[ci].suite_timeout_s must be a positive integer -- without it"
                    " one hung suite wedges the whole tier")

    ceiling = ci.get("max_exempt_suites")
    if ceiling is None:
        viol.append("[ci] has no max_exempt_suites -- an absent ceiling is a broken"
                    " ratchet, not an open one")
    elif len(exempt) > int(ceiling):
        viol.append(f"exempt suites {len(exempt)} > ceiling {ceiling}")

    print("\n".join(viol))
    if not viol:
        print(f"[ci-suites] {len(reg)} suite(s) registered across "
              f"{len(set(reg.values()))} tier(s); {len(exempt)}/{ceiling} exempt",
              file=sys.stderr)
    return 1 if viol else 0

# The dev container IS the MiOS image plus container wiring, so anything that
# would make it a different image is refused: a second stage, a FROM other than
# the ARG, an ARG default other than [image].ref, a file brought in from the
# build context, or a RUN that installs a package, toolchain or payload. A
# component the devcontainer lacks belongs in the OS pipeline instead.
DEV_CONTAINERFILE = ".devcontainer/Containerfile"
_DEV_INSTALL = re.compile(
    r"\b(?:dnf5?|microdnf|yum|apt-get|apt|apk|zypper|rpm-ostree|flatpak)\s+(?:-\S+\s+)*"
    r"(?:install|in|reinstall|upgrade|update|groupinstall|add)\b"
    r"|\brpm\s+-[A-Za-z]*[iUF]"
    r"|\bpip[0-9.]*\s+install\b|-m\s+pip\s+install\b|\b(?:pipx|uv\s+tool|uv\s+pip)\s+install\b"
    r"|\b(?:npm|pnpm|yarn|bun)\s+(?:install|i|ci|add)\b"
    r"|\brustup(?:-init)?\b|\bcargo\s+(?:install|build)\b"
    r"|\b(?:curl|wget)\b|\bgit\s+clone\b"
    r"|\bmios-mcp-server\b|\b[0-9]{2}-[a-z0-9-]+\.sh\b")


def devcontainer_violations(text: str, image_ref: str) -> list:
    """Every way `text` (a dev Containerfile) stops being [image].ref plus wiring."""
    logical, buf = [], ""
    for raw in text.splitlines():
        line = raw.strip()
        if not buf and (not line or line.startswith("#")):
            continue
        if buf and line.startswith("#"):
            continue  # a comment inside a continued instruction
        if line.endswith("\\"):
            buf += line[:-1] + " "
            continue
        logical.append(buf + line)
        buf = ""
    if buf:
        logical.append(buf)
    viol = []
    froms = [i for i, ln in enumerate(logical) if ln.split(None, 1)[0].upper() == "FROM"]
    if len(froms) != 1:
        viol.append(f"devcontainer Containerfile has {len(froms)} FROM lines; it must be one stage, FROM ${{MIOS_IMAGE}}")
    elif logical[froms[0]].split()[1:] != ["${MIOS_IMAGE}"]:
        viol.append(f"devcontainer {logical[froms[0]]!r} is not FROM ${{MIOS_IMAGE}}")
    first = froms[0] if froms else len(logical)
    args = [ln for ln in logical[:first] if re.match(r"ARG\s+MIOS_IMAGE(?:=|\s|$)", ln, re.I)]
    default = args[0].split("=", 1)[1].strip().strip("'\"") if args and "=" in args[0] else None
    if default != image_ref:
        viol.append(f"devcontainer ARG MIOS_IMAGE default {default!r} differs from [image].ref {image_ref!r}")
    for ln in logical:
        op = ln.split(None, 1)[0].upper()
        if op in ("COPY", "ADD"):
            viol.append(f"devcontainer brings files into the image ({ln[:60]!r}); it adds only wiring")
        elif op == "RUN":
            hit = _DEV_INSTALL.search(ln)
            if hit:
                viol.append(f"devcontainer RUN installs {hit.group(0)!r}; install it in the OS image pipeline")
    return viol


def devcontainer_check(root: str) -> list:
    """The repo's dev Containerfile against the SSOT, and the local build's layering."""
    with open(os.path.join(root, "usr/share/mios/mios.toml"), "rb") as fh:
        data = tomllib.load(fh)
    ref = (data.get("image") or {}).get("ref")
    if not ref:
        return ["[image].ref is empty -- the dev container has no image to be"]
    path = os.path.join(root, DEV_CONTAINERFILE)
    if not os.path.isfile(path):
        return [f"{DEV_CONTAINERFILE} is missing"]
    viol = devcontainer_violations(Path(path).read_text(encoding="utf-8"), ref)
    images = (data.get("build") or {}).get("images") or {}
    os_tag = (images.get("os") or {}).get("tag_key")
    dev_arg = ((images.get("devcontainer") or {}).get("build_args") or {}).get("MIOS_IMAGE")
    if not os_tag or dev_arg != os_tag:
        viol.append(f"[build.images.devcontainer].build_args.MIOS_IMAGE {dev_arg!r} is not the os"
                    f" target's tag_key {os_tag!r}: a local dev build would not layer on the image it built")
    return viol


def suite_timeout(ci: dict):
    """[ci].suite_timeout_s as a positive int, else None (bool is not a count)."""
    v = ci.get("suite_timeout_s")
    return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else None

def main(argv: list) -> int:
    root = _root()
    try:
        ci = _load(root)
    except (OSError, ValueError) as exc:
        print(f"mios.toml unreadable: {exc}", file=sys.stderr)
        return 1
    if not ci:
        print("mios.toml has no [ci] table -- the suite registry is the only"
              " thing that keeps the publishers running the same set")
        return 1
    if "--check" in argv:
        return cmd_check(root, ci)
    if "--python-packages" in argv:
        py = ci.get("python") or {}
        args = []
        for req in (py.get("requirements") or ()):
            args += ["-r", req]
        args += list(py.get("packages") or ())
        print(" ".join(args))
        return 0
    for option in ("--dnf-repos", "--dnf-packages", "--fedora-image"):
        if option in argv:
            try:
                args = fedora_arguments(root, ci, option)
            except (OSError, ValueError, TypeError) as exc:
                print(f"{option}: {exc}", file=sys.stderr)
                return 1
            print(" ".join(args))
            return 0
    if "--suite-timeout" in argv:
        t = suite_timeout(ci)
        if t is None:
            print("[ci].suite_timeout_s is absent or not a positive integer", file=sys.stderr)
            return 1
        print(t)
        return 0
    if "--tool-skips" in argv:
        print("\n".join(sorted(ci.get("tool_skips") or {})))
        return 0
    for i, a in enumerate(argv):
        if a == "--tier" and i + 1 < len(argv):
            return cmd_list(root, ci, argv[i + 1])
        if a.startswith("--tier="):
            return cmd_list(root, ci, a.split("=", 1)[1])
    print("usage: ci-suites.py --tier <name> | --check | --python-packages | --dnf-repos | --dnf-packages | --fedora-image | --tool-skips | --suite-timeout",
          file=sys.stderr)
    return 2

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
