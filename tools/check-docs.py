#!/usr/bin/env python3
# AI-hint: Documentation-plane drift gates in one module: ratchet monotonicity, manual links, comment-lexer equivalence, header comment syntax, generated prose in resolvers, redaction coverage. The subcommand selects the gate.
# AI-doc: usr/share/doc/mios/manual/tools.md
"""Documentation-plane drift gates. One module, one subcommand per gate."""
import sys


import os
import sys

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore

drm__HERE = os.path.dirname(os.path.abspath(__file__))
drm_ROOT = os.path.abspath(os.path.join(drm__HERE, ".."))

def drm_read_floor(path: str) -> dict[str, int]:
    out: dict[str, int] = {}
    if not os.path.isfile(path):
        return out
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("#") or not line.strip():
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) == 2 and parts[1].strip().lstrip("-").isdigit():
                out[parts[0].strip()] = int(parts[1])
    return out

def drm_main() -> int:
    ssot_path = os.path.join(drm_ROOT, "usr", "share", "mios", "mios.toml")
    with open(ssot_path, "rb") as fh:
        ssot = tomllib.load(fh)

    docs = ssot.get("docs", {}) or {}
    ai_tag = ssot.get("ai_tag", {}) or {}
    ceilings = {
        "max_unmigrated_narrative": int(docs.get("max_unmigrated_narrative", 0)),
        "max_stale_refs": int(docs.get("max_stale_refs", 0)),
        "max_overlong_hints": int(ai_tag.get("max_overlong_hints", docs.get("max_overlong_hints", 0))),
        "max_undocumented_components": int(docs.get("max_undocumented_components", 0)),
    }

    floor_path = os.path.join(drm_ROOT, "usr", "share", "mios", "reference", "doc-ratchet-floor.tsv")
    floors = drm_read_floor(floor_path)

    violations = []
    for key, curr in ceilings.items():
        if key in floors:
            recorded = floors[key]
            if curr > recorded:
                violations.append(
                    f"ceiling for '{key}' in mios.toml ({curr}) exceeds recorded monotone floor in doc-ratchet-floor.tsv ({recorded})"
                )

    if violations:
        for v in violations:
            print(f"check_doc_ratchet_monotone: {v}", file=sys.stderr)
        return 1

    print("check_doc_ratchet_monotone OK: all ceilings <= monotone floor baseline")
    return 0


"""Fail if the manual's ToC points at a chapter file or anchor that is not there."""
import os
import re
import sys

ml_ROOT = os.environ.get("MIOS_ROOT", ".")
ml_DOCS = os.path.join(ml_ROOT, "usr/share/doc/mios")
ml_MANUAL = os.path.join(ml_DOCS, "manual.md")
ml_LINK_RE = re.compile(r"\[([^\]]*)\]\((manual/ch[^)]+)\)")
# Only ./x and ../x: a bare `usr/share/...` is repo-root-relative by convention
# and resolving it as file-relative would invent 190 false findings.
ml_REL_RE = re.compile(r"\[[^\]]*\]\((\.{1,2}/[^)#\s]+)(?:#[^)\s]*)?\)")
ml_ANCHOR_RE = re.compile(r'<a\s+name="([^"]+)"', re.I)

def ml_relative_link_violations() -> list:
    """Every ./x or ../x link under the docs tree must resolve."""
    viol = []
    for dirpath, _dirnames, filenames in os.walk(ml_DOCS):
        for fn in sorted(filenames):
            if not fn.endswith(".md"):
                continue
            src = os.path.join(dirpath, fn)
            try:
                with open(src, encoding="utf-8", errors="replace") as fh:
                    body = fh.read()
            except OSError:
                continue
            for m in ml_REL_RE.finditer(body):
                target = m.group(1)
                if not os.path.exists(os.path.normpath(
                        os.path.join(dirpath, target))):
                    viol.append("%s links to %s, which does not exist"
                                % (os.path.relpath(src, ml_ROOT).replace(os.sep, "/"),
                                   target))
    return viol

def ml_main() -> int:
    if not os.path.isfile(ml_MANUAL):
        print(f"manual entry point missing: {ml_MANUAL}", file=sys.stderr)
        return 1
    text = open(ml_MANUAL, encoding="utf-8").read()
    links = ml_LINK_RE.findall(text)
    bad = []
    for _, target in links:
        path, _, frag = target.partition("#")
        full = os.path.join(ml_DOCS, path)
        if not os.path.isfile(full):
            bad.append(f"manual.md -> missing chapter file: {target}")
            continue
        if frag:
            anchors = set(ml_ANCHOR_RE.findall(open(full, encoding="utf-8").read()))
            if frag not in anchors:
                bad.append(f"manual.md -> missing anchor: {target}")
    referenced = {t.partition("#")[0] for _, t in links}
    chapters = sorted(
        "manual/" + f
        for f in os.listdir(os.path.join(ml_DOCS, "manual"))
        if f.startswith("ch") and f.endswith(".md")
    )
    ch_nums = {}
    for c in chapters:
        m = re.match(r"^ch(\d+)-", os.path.basename(c))
        if m:
            ch_nums.setdefault(int(m.group(1)), []).append(c)
    for num, files in sorted(ch_nums.items()):
        if len(files) > 1:
            bad.append(f"duplicate chapter number {num:02d}: {', '.join(files)}")
    bad += [f"chapter unreachable from the ToC: {c}" for c in chapters if c not in referenced]
    rel = ml_relative_link_violations()
    bad += rel
    if bad:
        print("\n".join(bad), file=sys.stderr)
        return 1
    print(f"manual links resolve ({len(links)} ToC links, {len(chapters)} chapters); "
          f"every explicitly-relative doc link resolves")
    return 0


import os
import sys

cle__HERE = os.path.dirname(os.path.abspath(__file__))
cle__REPO_ROOT = os.path.abspath(os.path.join(cle__HERE, ".."))
sys.path.insert(0, os.path.join(cle__REPO_ROOT, "usr", "lib", "mios"))

import mios_comments

# Every field a consumer reads; comparing hashes alone missed spans and headers.
cle_FIELDS = ("start_line", "end_line", "kind", "style", "text", "norm", "sha12",
              "lines", "words", "attach", "anchor_code", "in_header_block")


def cle_main():
    import json
    import subprocess
    root = os.environ.get("MIOS_DRIFT_ROOT", cle__REPO_ROOT)
    # The drift gate passes its resolver's answer; set but empty means none was found.
    native_bin = os.environ.get("MIOS_COMMENT_LEX_BIN", mios_comments._find_native_comment_lex())

    if not native_bin:
        if os.environ.get("MIOS_DRIFT_REQUIRE_TOOLS") == "1":
            print("[check-comment-lex] ERROR: mios-comment-lex is not built, so the"
                  " lexers were never compared (MIOS_DRIFT_REQUIRE_TOOLS=1)")
            return 1
        print("[check-comment-lex] SKIPPED: mios-comment-lex binary not present (optional native tier)")
        return 0

    print(f"[check-comment-lex] Testing differential equivalence using {native_bin}")

    mismatches = []
    files_tested = 0

    for rel, full in mios_comments.iter_source_files(root):
        if rel.endswith(".py"):
            continue  # Python files use AST docstring lexer in Python
        files_tested += 1

        with open(full, "rb") as fh:
            raw = fh.read()
        py_blocks = mios_comments._lex_generic(
            full,
            raw.decode("utf-8-sig", errors="replace").replace("\r\n", "\n"),
            mios_comments._style_for(full),
        )
        py_rows = [tuple(getattr(b, f) for f in cle_FIELDS) for b in py_blocks]

        try:
            proc = subprocess.run([native_bin, full], capture_output=True, check=True)
            records = json.loads(proc.stdout.decode("utf-8"))
            native_rows = [tuple(r[f] for f in cle_FIELDS) for r in records]
        except Exception as exc:
            mismatches.append(f"{rel}: native lexer execution failed: {exc}")
            continue

        if py_rows != native_rows:
            only_py = [r for r in py_rows if r not in native_rows]
            only_nat = [r for r in native_rows if r not in py_rows]
            first = (only_py or only_nat or py_rows)[0]
            mismatches.append(f"{rel}: {len(py_rows)} python vs {len(native_rows)} native"
                              f" blocks; first differing at lines {first[0]}-{first[1]}:"
                              f" python-only {only_py[:1]} native-only {only_nat[:1]}")

    print(f"[check-comment-lex] Tested {files_tested} non-python source files.")
    if not files_tested:
        print("[check-comment-lex] ERROR: no source file was compared, so equivalence is unproven")
        return 1
    if mismatches:
        print(f"[check-comment-lex] ERROR: {len(mismatches)} files lex differently:")
        for m in mismatches[:10]:
            print(f"  {m[:600]}")
        return 1

    print("[check-comment-lex] SUCCESS: Python and native lexers are equivalent!")
    return 0


import os
import re
import subprocess
import sys

# Formats whose comment character is #. A C-style header in one of these is not
# a comment at all: systemd rejects the line, and an INI parser may too. One
# such line in usr/lib/wsl.conf drifted from its /etc twin and failed a build
# twenty-nine minutes in.
hcs_HASH_COMMENT = (".conf", ".service", ".socket", ".timer", ".target", ".mount",
                ".path", ".network", ".container", ".pod", ".volume", ".toml",
                ".ini", ".cfg", ".repo", ".preset", ".sh", ".py", ".yml",
                ".yaml", ".nft", ".rules")
hcs_BAD = re.compile(r"^/\*\s*AI-(?:doc|hint|related):", re.M)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mios_tracked import tracked, GitUnavailable  # noqa: E402

def hcs_main() -> int:
    root = os.environ.get("MIOS_DRIFT_ROOT") or os.getcwd()
    try:
        paths = tracked(root)
    except GitUnavailable as exc:
        print("check-header-comment-syntax: %s" % exc, file=sys.stderr)
        return 1
    viol = []
    for rel in sorted(paths):
        if os.path.splitext(rel)[1] not in hcs_HASH_COMMENT:
            continue
        full = os.path.join(root, rel)
        try:
            with open(full, encoding="utf-8", errors="ignore") as fh:
                s = fh.read()
        except OSError:
            continue
        if hcs_BAD.search(s):
            viol.append("%s carries a C-style AI header, but this format comments"
                        " with #" % rel)
    print("\n".join(viol[:20]))
    if viol:
        if len(viol) > 20:
            print("... and %d more" % (len(viol) - 20))
        return 1
    print("[check-header-comment-syntax] every AI header uses its format's comment"
          " character", file=sys.stderr)
    return 0


import os
import re
import sys

ngp__HERE = os.path.dirname(os.path.abspath(__file__))
# Honour the root the caller names, as every sibling checker does. Hardcoding it
# to this file's location made the tool impossible to aim at a fixture or at the
# bootstrap repo, so it could only ever be exercised against the live tree.
ngp_ROOT = os.environ.get("MIOS_DRIFT_ROOT") or os.environ.get("MIOS_ROOT")     or os.path.abspath(os.path.join(ngp__HERE, ".."))

ngp_TARGETS = [
    os.path.join(ngp_ROOT, "automation", "lib", "globals.sh"),
    os.path.join(ngp_ROOT, "automation", "lib", "globals.ps1"),
]

ngp_COMMENT_RE = re.compile(r"MIOS_UNITS_[A-Z0-9_]*_COMMENT=")

def ngp_main() -> int:
    violations = []
    for path in ngp_TARGETS:
        if not os.path.isfile(path):
            continue
        rel = os.path.relpath(path, ngp_ROOT).replace(os.sep, "/").replace("\\", "/")
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line_no, line in enumerate(fh, 1):
                if "AI-hint:" in line:
                    violations.append(f"{rel}:{line_no} contains prose header 'AI-hint:'")
                if ngp_COMMENT_RE.search(line):
                    violations.append(f"{rel}:{line_no} contains unit comment assignment 'MIOS_UNITS_*_COMMENT='")

    if violations:
        for v in violations:
            print(f"check_no_generated_prose_in_resolvers: {v}", file=sys.stderr)
        return 1

    print("check_no_generated_prose_in_resolvers OK: zero AI-hint prose or unit comment values in resolvers")
    return 0


"""Fail if a pgvector table is neither redacted nor explicitly exempt on persist."""
import os
import re
import sys
import tomllib

rc_ROOT = os.environ.get("MIOS_ROOT", ".")
rc_SSOT = os.path.join(rc_ROOT, "usr/share/mios/mios.toml")
rc_SCHEMA = os.path.join(rc_ROOT, "usr/share/mios/postgres/schema-init.sql")
rc_PG = os.path.join(rc_ROOT, "usr/lib/mios/agent-pipe/mios_pipe/memory/pg.py")
# Free-text agent surfaces that must never drop off the redact side.
rc_MUST_REDACT = {"knowledge", "agent_memory", "event", "tool_call", "scratch"}

def rc_main() -> int:
    cfg = (tomllib.load(open(rc_SSOT, "rb")).get("security", {}) or {}).get("redact", {}) or {}
    tables = set(cfg.get("tables", []))
    exempt = set(cfg.get("exempt", []))
    schema = set(re.findall(r"CREATE TABLE (?:IF NOT EXISTS )?([a-z_]+)",
                            open(rc_SCHEMA, encoding="utf-8").read()))
    bad = []
    for t in sorted(schema - tables - exempt):
        bad.append(f"schema table classified in NEITHER redact nor exempt: {t}")
    for t in sorted(tables & exempt):
        bad.append(f"table classified in BOTH redact and exempt: {t}")
    for t in sorted((tables | exempt) - schema):
        bad.append(f"classified table absent from the schema: {t}")
    for t in sorted(rc_MUST_REDACT - tables):
        bad.append(f"free-text agent table must stay redacted: {t}")
    if os.path.isfile(rc_PG):
        src = open(rc_PG, encoding="utf-8").read()
        # Only the REDACTION site: an unrelated ("knowledge", "agent_memory")
        # tuple (the embedding-version check) is not this defect.
        if re.search(r'for t in \(\s*"knowledge"', src):
            bad.append("memory/pg.py still hardcodes its redaction table tuple")
        if "_redact_cfg" not in src:
            bad.append("memory/pg.py does not read [security.redact] from the SSOT")
    if bad:
        print("\n".join(bad), file=sys.stderr)
        return 1
    print(f"persist redaction covers the schema "
          f"({len(tables)} redacted, {len(exempt)} exempt, {len(schema)} tables)")
    return 0

_GATES = {"ratchet-monotone": drm_main, "manual-links": ml_main, "comment-lex": cle_main, "header-syntax": hcs_main, "no-generated-prose": ngp_main, "redact-coverage": rc_main}


def main() -> int:
    # An unknown or missing subcommand must FAIL, never report a clean gate.
    if len(sys.argv) < 2 or sys.argv[1] not in _GATES:
        sys.stderr.write("usage: check-docs.py {%s}\n" % "|".join(sorted(_GATES)))
        return 2
    return _GATES[sys.argv.pop(1)]()


if __name__ == "__main__":
    sys.exit(main())
