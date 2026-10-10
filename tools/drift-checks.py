#!/usr/bin/env python3
# AI-hint: The three largest drift checks, lifted out of their shell heredocs so they can be imported, linted and tested.
# AI-related: mios_manifest, mios_capreg, mios_surface, mios_comments, /usr/libexec/mios/mios-resolver, /usr/share/mios/mios.toml, mios-resolver, mios-env-snapshot, mios-drift-ctx-test, mios-bootstrap
# AI-functions: check_resolver_differential_parity, check_legibility_ratchet, lines, _is_generated, check_no_inert_ssot_tables, check_no_duplicate_value_key, emit, esc, unesc, _shape, check_unwired_modules
"""Each subcommand is one check: it prints violations and exits non-zero.

They lived as heredocs inside the shell gate, where nothing could import or
lint them and a syntax error only surfaced when the check ran. The bodies are
unchanged -- only their container is.
"""
import sys
import os
import re

os.environ.setdefault("MIOS_DRIFT_ROOT", os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

def _absent(root: str, path: str):
    """None when path (file or directory) is there; else the status to return.
    Absent though TRACKED fails; a root that never had it still skips."""
    if os.path.exists(path):
        return None
    import subprocess
    rel = os.path.relpath(path, root).replace(os.sep, "/")
    if not os.path.exists(os.path.join(root, ".git")):
        return 0                      # not a checkout of this repo at all
    try:
        p = subprocess.run(["git", "-C", root, "ls-files", "--", rel],
                           capture_output=True, text=True)
    except OSError as exc:
        sys.stderr.write("    cannot tell whether %s is tracked: git could not "
                         "be run in %s: %s\n" % (rel, root, exc))
        return 1
    if p.returncode != 0:
        sys.stderr.write("    cannot tell whether %s is tracked: git ls-files "
                         "exit %d: %s\n" % (rel, p.returncode,
                                            (p.stderr or "").strip() or "no message"))
        return 1
    if not p.stdout.strip():
        return 0
    sys.stderr.write("    %s is tracked but missing from the worktree -- the "
                     "subject of this check is gone, which is not a pass\n" % rel)
    return 1

def _scan(root: str, *paths: str):
    """Subjects that are there; a tracked one that is gone exits 1, not 0."""
    seen = [(p, _absent(root, p)) for p in paths]
    for p, rc in seen:
        if rc: raise SystemExit(rc)
    return [p for p, rc in seen if rc is None]

def _tracked(root: str, *pathspec: str):
    """(paths, None) when git listed a corpus; (None, status) when it did not.

    mios_tracked.tracked() plus _absent's not-a-checkout skip. See f66e6efc.
    """
    if not os.path.exists(os.path.join(root, ".git")):
        return None, 0                # not a checkout of this repo at all
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from mios_tracked import GitUnavailable, tracked
    try:
        return tracked(root, *pathspec), None
    except (GitUnavailable, OSError) as exc:
        sys.stderr.write("    %s\n" % exc)
        return None, 1

def _under(path: str, root: str) -> str:
    """Repo-relative when the path is inside root, else the path as given."""
    rel = os.path.relpath(path, root)
    return path if rel.startswith("..") else rel.replace(os.sep, "/")

def check_resolver_differential_parity() -> int:
    import os, sys, subprocess
    import tomllib

    root = os.environ.get("MIOS_DRIFT_ROOT", ".")
    _toml_data = tomllib.load(open(os.path.join(root, "usr/share/mios/mios.toml"), "rb"))
    resolver_bin = None
    catalog = os.environ.get("MIOS_NATIVE_BIN_DIR")
    if catalog:
        resolver_bin = os.path.join(catalog, "mios-resolver" + (".exe" if sys.platform == "win32" else ""))
        if not os.path.isfile(resolver_bin) or not os.access(resolver_bin, os.X_OK):
            print(f"    configured resolver is missing or not executable: {resolver_bin}", file=sys.stderr)
            sys.exit(1)

    for cand in ([] if catalog else [os.path.join(root, "tools/native/target", p, "mios-resolver" + x)
                 for p in ("debug", "release") for x in ("", ".exe")] + [
                 "/usr/libexec/mios/mios-resolver", "/usr/bin/mios-resolver"]):
        if os.path.isfile(cand):
            resolver_bin = cand
            break

    if not resolver_bin:
        print("    mios-resolver is not built, so the Python/Rust resolvers were "
              "never compared. Build it: "
              "cd tools/native && cargo build -p mios-resolver", file=sys.stderr)
        sys.exit(1)

    # Both twins resolve the SAME tiers: the root's vendor file and drop-ins,
    # no host or user overlay, so the comparison depends on the tree alone.
    tiers = {"MIOS_TOML_ROOT": root,
             "MIOS_VENDOR_TOML": os.path.join(root, "usr/share/mios/mios.toml"),
             "MIOS_HOST_TOML": "/nonexistent/mios-host.toml",
             "MIOS_USER_TOML": "/nonexistent/mios-user.toml"}
    os.environ.update(tiers)
    _mt_dir = os.path.join(root, "usr", "lib", "mios")
    if _mt_dir not in sys.path:
        sys.path.insert(0, _mt_dir)
    import mios_toml as _mios_toml

    # The Python twin is mios_toml.emit_exports over a merge it performs ITSELF.
    # load_merged() with no layers hands the merge to mios-resolver whenever one
    # is on PATH, and the check would then measure the Rust resolver against
    # itself. emit_exports also resolves ${MIOS_*} cross-references, which is the
    # baked form --emit=json prints. (tools/render-globals.py, the old Python
    # side, was ported to `mios-gen render-globals` in 5776d4ff; importing it
    # crashed this check on every run since.)
    py_exports = _mios_toml.emit_exports(
        _mios_toml.load_merged(layers=_mios_toml.layer_paths()))

    try:
        res = subprocess.run([resolver_bin, "--emit=json"], capture_output=True, text=True,
                             check=True, env=dict(os.environ, **tiers))
        import json
        rs_exports = (_j := json.loads(res.stdout)).get("exports", _j)  # emit_json wraps: {merged, exports}
    except Exception as exc:
        print(f"    mios-resolver --emit=json execution failed: {exc}", file=sys.stderr)
        sys.exit(1)
    if not py_exports or not rs_exports:
        print(f"    a resolver emitted nothing (python {len(py_exports)}, rust "
              f"{len(rs_exports)}) -- an empty comparison is not parity", file=sys.stderr)
        sys.exit(1)

    _rc = _toml_data.get("resolver") or {}; ceil_div = _rc.get("max_key_divergence")
    diff_keys = set(py_exports) ^ set(rs_exports)
    if ceil_div is None or len(diff_keys) > int(ceil_div):
        print(f"    key divergence {len(diff_keys)} vs ceiling {ceil_div}: {sorted(diff_keys)[:10]}", file=sys.stderr)
        sys.exit(1)

    mismatches = []
    for k in sorted(set(py_exports) & set(rs_exports)):
        v_py = str(py_exports[k])
        v_rs = str(rs_exports[k])
        if v_py != v_rs:
            mismatches.append(f"{k}: py='{v_py}' vs rs='{v_rs}'")

    ceil_val = _rc.get("max_value_divergence")
    if ceil_val is None or len(mismatches) > int(ceil_val):
        print(f"    value divergence {len(mismatches)} vs ceiling {ceil_val}:", file=sys.stderr)
        for m in mismatches[:10]:
            print(f"      {m}", file=sys.stderr)
        sys.exit(1)
    print(f"    resolver divergence: {len(diff_keys)}/{ceil_div} keys, {len(mismatches)}/{ceil_val} values (shrink-only; task_2317)", file=sys.stderr)

    print("    mios-resolver --emit=json matches Python SSOT render 100%")
    sys.exit(0)

# A sibling unit test, by either naming convention in the tree.
_TEST_BASENAME = re.compile(r"^test[-_]")

def check_legibility_ratchet() -> int:
    import os, subprocess, sys
    import tomllib

    root = os.environ.get("MIOS_DRIFT_ROOT", ".")
    with open(os.path.join(root, "usr/share/mios/mios.toml"), "rb") as fh:
        config = tomllib.load(fh)
        lim = dict(config.get("legibility") or {})
    phase_cap = config.get("build", {}).get("ratchet", {}).get("max_phase_scripts")
    if type(phase_cap) is not int or phase_cap < 0:
        print("mios.toml [build.ratchet].max_phase_scripts must be a nonnegative integer")
        sys.exit(1)
    lim["max_automation_phases"] = phase_cap
    if not lim:
        print("mios.toml [legibility] is absent -- the size of the deliverable is "
              "then bounded by nothing")
        sys.exit(1)

    # "not a work tree; skipping" also answered a git that REFUSED, and every
    # ratchet below is computed from this listing.
    rels, _rc = _tracked(root)
    if rels is None:
        sys.stderr.write("[legibility] no tracked file was listed, so no "
                         "ratchet was measured\n")
        sys.exit(_rc)

    def lines(paths):
        n = 0
        for rel in paths:
            try:
                with open(os.path.join(root, rel.replace("/", os.sep)), "rb") as fh:
                    n += fh.read().count(b"\n")
            except OSError:
                pass
        return n

    # Size the deliverable from the INDEX blobs, not the checkout. .gitattributes
    # checks *.ps1 out as CRLF on every platform, so the working tree carries
    # ~24 KiB of line-ending expansion the commit does not contain -- and with the
    # total sitting a few KiB past the 201.5 MiB rounding boundary, that expansion
    # alone pushed tracked_mb to 202 and held this ratchet red against content
    # nobody added. Blobs are identical in every clean checkout of a commit.
    nbytes, unmerged = 0, []
    try:
        ls_s = subprocess.run(["git", "ls-files", "-s", "-z"], cwd=root,
                              capture_output=True, check=True).stdout.decode("utf-8", "replace")
        entries = [e.split("\t", 1) for e in ls_s.split("\0") if e.strip()]
        unmerged = sorted({p for m, p in entries if m.split()[2] != "0"})  # one entry per merge stage
        oids = [m.split()[1] for m, _ in entries]
        if oids and not unmerged:
            sizes = subprocess.run(["git", "cat-file", "--batch-check=%(objectsize)"],
                                   cwd=root, input="\n".join(oids).encode(),
                                   capture_output=True, check=True).stdout.decode()
            nbytes = sum(int(s) for s in sizes.split() if s.isdigit())
    except Exception:
        for rel in rels:
            try:
                nbytes += os.path.getsize(os.path.join(root, rel.replace("/", os.sep)))
            except OSError:
                pass
    if unmerged:
        sys.exit("[legibility] %d unmerged path(s) (first: %s) -- a mid-merge index has no "
                 "single size; resolve the merge, then measure" % (len(unmerged), unmerged[0]))

    def _is_generated(rel):
        """True for a file that declares itself a machine projection.

        The shell/PowerShell ceilings exist to drive HAND-WRITTEN glue down as it
        migrates to Rust. automation/lib/globals.{sh,ps1} are rendered in full from
        mios.toml, so they grow whenever the operator declares a config key -- growth
        that cannot be "earned back" except by deleting operator configuration. Counting
        them measured the wrong thing: a [cat] -> [field] rename that added keys pushed
        the PowerShell ceiling over its floor with no hand-written line involved.
        Excluding them LOWERS both floors by ~5.3k lines, so the ratchet binds strictly
        tighter on the code it actually governs.
        """
        try:
            with open(os.path.join(root, rel.replace("/", os.sep)),
                      encoding="utf-8", errors="replace") as fh:
                head = fh.read(600).upper()
        except OSError:
            return False
        return "GENERATED" in head and "DO NOT EDIT" in head

    _ai_plane = tuple(lim.get("python_ai_plane_prefixes") or ())

    measured = {
        "max_tracked_files": len(rels),
        "max_tracked_mb": round(nbytes / 1048576),
        "max_shell_lines": lines([r for r in rels
                                  if r.endswith((".sh", ".bash")) and not _is_generated(r)]),
        "max_ps_lines": lines([r for r in rels
                               if r.endswith((".ps1", ".psm1")) and not _is_generated(r)]),
        # ADR-0021. Law 14 keeps the AI plane in Python, so it is exempt by
        # prefix from SSOT rather than by a list baked in here.
        # A sibling unit test is not tooling to port. Counting them made this
        # ratchet pull against check_module_test_coverage the same way
        # max_libexec_verbs did below, and 36% of what it measured was test
        # code. Floor re-baselined down by what the exclusion removes (task_981).
        "max_tooling_python_lines": lines([
            r for r in rels
            if r.endswith(".py") and not _is_generated(r)
            and not _TEST_BASENAME.match(r.rsplit("/", 1)[-1])
            and not any(r.startswith(pfx) for pfx in _ai_plane)]),
        "max_automation_phases": len([r for r in rels if r.startswith("automation/")
                                      and r.endswith(".sh") and r[11:13].isdigit()]),
        # Sibling unit tests are not verbs. Counting them made this ratchet pull
        # against check_module_test_coverage: adding the test that gate demands
        # tripped this one, so the cheapest way to stay green was to not write
        # the test. Floor re-baselined down by the 16 already present.
        "max_libexec_verbs": len([r for r in rels if r.startswith("usr/libexec/mios/")
                                  and r.count("/") == 3
                                  and not _TEST_BASENAME.match(r.rsplit("/", 1)[-1])]),
    }
    # ADR-0030: 500 tracked files EACH per MiOS repo, dependencies included (the
    # operator: "500 total files EACH per MiOS repo(s)"). This repo's files are
    # partitioned into the domains below; its sister repo mios-bootstrap has its
    # own shrink-only ceiling and target. max_tracked_files is this repo's count,
    # which ratchet-direction also compares with main's tree.
    import fnmatch
    viol = []
    source = rels
    bs_root = os.environ.get("MIOS_BOOTSTRAP_ROOT") or os.path.join(root, "..", "mios-bootstrap")
    bs_rels, _bs_rc = _tracked(bs_root) if os.path.isdir(bs_root) else (None, 1)
    if bs_rels is None:
        # Without the sister repo its budget is unmeasured, which is a violation,
        # never a zero that happens to pass.
        viol.append("mios-bootstrap is not a checkout at %s (set MIOS_BOOTSTRAP_ROOT): "
                    "its file budget cannot be measured" % bs_root)
    else:
        measured["max_bootstrap_files"] = len(bs_rels)

    # The budget fails closed: an absent table, a missing or non-integer target, or a
    # malformed domain is a violation, never a skipped comparison (peer-lane review of
    # af498021 planted all four and each passed).
    budget = lim.get("file_budget")
    if not isinstance(budget, dict) or not budget:
        viol.append("[legibility.file_budget] is absent -- ADR-0030's 500-file budget is then bounded by nothing")
        budget = {}
    target = budget.get("target_tracked_files")
    for key in ("target_tracked_files", "target_bootstrap_files"):
        v = budget.get(key)
        if budget and (type(v) is not int or v <= 0):
            viol.append("[legibility.file_budget].%s must be a positive integer (got %r)" % (key, v))
    if "max_bootstrap_files" not in lim:
        viol.append("[legibility].max_bootstrap_files is absent -- the sister repo's ceiling is then bounded by nothing")
    order, domains = budget.get("order"), budget.get("domains")
    if budget and (not isinstance(order, list) or not order or not isinstance(domains, dict) or not domains):
        viol.append("[legibility.file_budget] needs a non-empty order list and domains table")
    order = order if isinstance(order, list) else []
    domains = domains if isinstance(domains, dict) else {}
    for d in order:
        spec = domains.get(d) if isinstance(domains.get(d), dict) else {}
        for key in ("max_files", "target_files"):
            v = spec.get(key)
            if type(v) is not int or v < 0:
                viol.append("[legibility.file_budget.domains.%s].%s must be a non-negative integer (got %r)" % (d, key, v))
        g = spec.get("globs")
        if not isinstance(g, list) or not g or not all(isinstance(x, str) and x for x in g):
            viol.append("[legibility.file_budget.domains.%s].globs must be a non-empty list of strings" % d)
    # Each source file lands in the FIRST domain (in [legibility.file_budget].order)
    # whose glob matches it, so a file is never budgeted twice or not at all.
    per_domain = {}
    if budget:
        missing = [d for d in order if d not in domains] + [d for d in domains if d not in order]
        if missing:
            viol.append("[legibility.file_budget] order and domains disagree on: %s" % ", ".join(missing))
        counts = dict.fromkeys(order, 0)
        homeless = []
        for rel in source:
            home = next((d for d in order if d in domains and any(
                fnmatch.fnmatchcase(rel, g) for g in domains[d].get("globs") or ())), None)
            if home is None:
                homeless.append(rel)
            else:
                counts[home] += 1
        if homeless:
            viol.append("%d tracked file(s) belong to no [legibility.file_budget] domain (first: %s)"
                        % (len(homeless), homeless[0]))
        for d in order:
            spec = domains.get(d) if isinstance(domains.get(d), dict) else {}
            cap = spec.get("max_files")
            per_domain[d] = (counts.get(d, 0), cap, spec.get("target_files"))
            if type(cap) is int and counts.get(d, 0) > cap:
                viol.append("files.%s = %d, over the floor of %d. This ratchet only comes DOWN: "
                            "fold or delete, do not raise it." % (d, counts[d], cap))
        # Only reached with every target an int (malformed ones were reported above).
        planned = sum(t for _, _, t in per_domain.values() if type(t) is int)
        if type(target) is int and planned > target:
            viol.append("[legibility.file_budget] domain targets sum to %d, over target_tracked_files = %d"
                        % (planned, target))
    for k, got in sorted(measured.items()):
        cap = lim.get(k)
        if cap is None:
            continue
        if got > cap:
            viol.append("%s = %d, over the floor of %d. This ratchet only comes DOWN: "
                        "fold or delete, do not raise it." % (k.replace("max_", ""), got, cap))
    print("[legibility] " + "  ".join("%s=%d/%s" % (k.replace("max_", ""), v, lim.get(k, "-"))
                                      for k, v in sorted(measured.items())), file=sys.stderr)
    if per_domain:
        print("[legibility] files toward target %s: " % budget.get("target_tracked_files", "-")
              + "  ".join("files.%s=%d/%s(->%s)" % (d, n, cap, t) for d, (n, cap, t) in per_domain.items()),
              file=sys.stderr)
    print("\n".join(viol))
    sys.exit(1 if viol else 0)

def check_legacy_archive() -> int:
    """ADR-0030 Decision 7: `mios-gen archive --check` compares .archive with the dropped set;
    its cannot-run exit (2) stays a failure."""
    import subprocess
    root = os.environ.get("MIOS_DRIFT_ROOT", ".")
    if os.environ.get("CTX") or root.rstrip("/") == "/tmp/build":
        # Gates are out of the bake; the drift-gate job runs this on full history.
        print("legacy archive: SKIPPED in-image OCI build (the drift-gate job enforces it on full history)")
        return 0
    exe = ".exe" if sys.platform == "win32" else ""
    catalog = os.environ.get("MIOS_NATIVE_BIN_DIR")
    if catalog:
        gen = os.path.join(catalog, "mios-gen" + exe)
        if not os.path.isfile(gen) or not os.access(gen, os.X_OK):
            print("configured mios-gen is missing or not executable: %s" % gen)
            return 1
    else:
        found = [c for c in [os.path.join(root, "tools/native/target", p, "mios-gen" + exe)
                             for p in ("release", "debug")]
                 + ["/usr/libexec/mios/mios-gen", "/usr/bin/mios-gen"] if os.path.isfile(c)]
        if not found:
            print("mios-gen is not built, so the legacy archive was never compared -- "
                  "build it: cd tools/native && cargo build -p mios-gen")
            return 1
        gen = found[0]
    try:
        proc = subprocess.run([gen, "archive", "--check", "--root", root],
                              capture_output=True, text=True)
    except OSError as exc:
        print("%s archive --check could not run: %s" % (gen, exc))
        return 1
    out = "\n".join(s for s in (proc.stdout.strip(), proc.stderr.strip()) if s)
    if proc.returncode == 0 and out:
        print(out)
        return 0
    print(out or "%s archive --check exited %d with no output" % (gen, proc.returncode))
    return proc.returncode if proc.returncode in (1, 2) else 1

def _value_sources(root: str):
    """(name -> declaring SSOT key path, None) from `mios-resolver --emit=names`,
    run sealed like mios-env-snapshot; else (None, why)."""
    import json, shutil, subprocess
    binary = os.environ.get("MIOS_RESOLVER_BIN") or shutil.which("mios-resolver")
    if not binary:
        return None, ("mios-resolver is not built, so no name can be traced to its "
                      "declaration -- build it: cd tools/native && cargo build -p mios-resolver")
    env = {"PATH": os.environ.get("PATH", ""), "HOME": "/nonexistent",
           "XDG_CONFIG_HOME": "/nonexistent/.config", "MIOS_ROOT": root, "MIOS_TOML_ROOT": root,
           "MIOS_VENDOR_TOML": os.environ.get("MIOS_VENDOR_TOML")
           or os.path.join(root, "usr/share/mios/mios.toml"),
           "MIOS_USER_TOML": "/nonexistent/user.toml", "MIOS_USER_TOML_D": "/nonexistent/user.d"}
    try:
        proc = subprocess.run([binary, "--emit=names"], capture_output=True, text=True, env=env)
        sources = json.loads(proc.stdout).get("sources") if proc.returncode == 0 else None
    except (OSError, ValueError, AttributeError) as exc:
        return None, "%s --emit=names produced no registry: %s" % (binary, exc)
    if not isinstance(sources, dict) or not sources:
        tail = (proc.stderr or "").strip().splitlines()
        return None, ("%s --emit=names exited %d with no provenance map%s" % (
            binary, proc.returncode, ": " + tail[-1] if tail else
            " (a resolver older than the `sources` field)"))
    return sources, None

# A keep-distinct row both value gates honour carries at least this much reason.
KEEP_DISTINCT_MIN_REASON = 20

def _alias_rows(tsv: str):
    """value-aliases.tsv rows as (line, canonical, alias, disposition, reason)."""
    rows = []
    with open(tsv, encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, 1):
            raw = raw.rstrip("\n")
            if not raw.strip() or raw.lstrip().startswith("#"):
                continue
            parts = raw.split("\t")
            if len(parts) < 3 or not parts[2].split():
                continue
            rest = "\t".join(parts[2:])
            rows.append((lineno, parts[0].strip(), parts[1].strip(), parts[2].split()[0].strip(),
                         rest.split("#", 1)[1].strip() if "#" in rest else ""))
    return rows

def check_no_duplicate_value_key() -> int:
    """One value, one name, ratcheted against the baseline ledger.

    Lifted out of its heredoc in the shell gate: 211 lines that nothing
    could import or lint, where a syntax error surfaced only when the
    check ran.
    """
    import os as _os
    import sys as _sys
    # Callable with no arguments: a caller that omits them gets the shipped
    # paths rather than an IndexError, which is what a bare invocation raised.
    _rest = _sys.argv[2:]
    if len(_rest) < 2:
        _root = (_os.environ.get("MIOS_DRIFT_ROOT")
                 or _os.environ.get("MIOS_ROOT") or _os.getcwd())
        _rest = [_os.path.join(_root, "usr/libexec/mios/mios-env-snapshot"),
                 _os.path.join(_root, "usr/share/mios/reference/value-dup-baseline.tsv")]
    _sys.argv = [__file__] + _rest
    import os
    import subprocess
    import sys

    snap_tool, baseline_path = sys.argv[1], sys.argv[2]
    BUMP = os.environ.get("MIOS_VALUE_DUP_BASELINE_BUMP", "0") == "1"

    # Well-known/protocol values only. A MiOS-allocated port must NOT be listed
    # here -- 8222 (the old ssh port) sat in this set and silently went dead when
    # [ports.categories] moved ssh, which is exactly how a stale exemption hides a
    # real duplicate.
    EXEMPT_VALUES = {"", "true", "false", "0", "1", "80", "443", "8080", "53", "22"}

    DEFAULT_HEADER = [
        "# value-dup-baseline.tsv -- ratcheted exemption ledger for",
        "# automation/98-drift-checks.sh::check_no_duplicate_value_key (WS-GUP task_2063).",
        "# Regenerate: MIOS_VALUE_DUP_BASELINE_BUMP=1 bash automation/98-drift-checks.sh check_no_duplicate_value_key",
        "# Format: value<TAB>key_count<TAB>comma-separated MIOS_* keys  (value escapes \\\\ \\t \\r)",
    ]

    def emit(msg):
        sys.stderr.write("    [value-dup-drift] " + msg + "\n")

    def esc(text):
        out = text.replace("\\", "\\\\").replace("\t", "\\t").replace("\r", "\\r")
        # A value may legitimately BE a comment -- systemd unit comments are
        # projected into MIOS_*_COMMENT keys -- so a leading "#" has to be escaped
        # or the writer emits 109 rows the reader then discards as comments, and
        # the ledger silently disagrees with the tree it was generated from.
        if out.startswith("#"):
            out = "\\#" + out[1:]
        return out

    def unesc(text):
        out = []
        i = 0
        while i < len(text):
            ch = text[i]
            if ch == "\\" and i + 1 < len(text):
                nxt = text[i + 1]
                if nxt == "t":
                    out.append("\t")
                    i += 2
                    continue
                if nxt == "r":
                    out.append("\r")
                    i += 2
                    continue
                if nxt == "#":
                    out.append("#")
                    i += 2
                    continue
                if nxt == "\\":
                    out.append("\\")
                    i += 2
                    continue
            out.append(ch)
            i += 1
        return "".join(out)

    # --- resolve the live environment -------------------------------------------
    # Git Bash cannot resolve an absolute path given as a script argument when
    # bash.exe is launched from Windows Python: both C:/MiOS/... and /c/MiOS/...
    # exit 127 "No such file", because /c is resolved against the MSYS root
    # rather than the drive. The same file runs when passed RELATIVE to a cwd.
    # The gate passes an absolute $ROOT, so on Windows this check reported "the
    # resolver produced no environment" -- a gate that could not run at all,
    # rather than one that passed or failed.
    _cwd = os.path.dirname(os.path.abspath(snap_tool)) or None
    _snap = os.path.basename(snap_tool)
    proc = subprocess.run(["bash", _snap], capture_output=True, text=True,
                          errors="replace", cwd=_cwd)
    if proc.returncode != 0:
        emit("mios-env-snapshot exited %d -- the resolver produced no environment, so this gate has no data" % proc.returncode)
        for tail in (proc.stderr or "").strip().splitlines()[-5:]:
            emit("  snapshot stderr: " + tail)
        sys.exit(1)

    env = {}
    for raw in proc.stdout.splitlines():
        raw = raw.strip()
        if not raw.startswith("MIOS_") or "=" not in raw:
            continue
        key, val = raw.split("=", 1)
        env[key] = val

    by_value = {}
    for key, val in env.items():
        by_value.setdefault(val, []).append(key)

    # A duplicate is two DECLARATIONS sharing one value. The resolver traces each
    # name to its key path; an untraced name stands alone.
    root = os.environ.get("MIOS_TOML_ROOT") or os.path.abspath(
        os.path.join(os.path.dirname(os.path.abspath(snap_tool)), "..", "..", ".."))
    sources, why = _value_sources(root)
    if sources is None:
        emit("cannot tell aliases from duplicates: " + why)
        sys.exit(1)

    def _shape(name):
        return ("decl", sources[name]) if name in sources else ("name", name.replace("_", ""))

    def _rep(name_set):
        """The one name a ledger row records for a declaration: its canonical."""
        ident = _shape(next(iter(name_set)))
        if ident[0] == "decl":
            body = re.sub(r"[^A-Z0-9_]", "_", ident[1].upper())  # names::canonical_name
            canon = body if body.startswith("MIOS_") else "MIOS_" + body
            if canon in name_set:
                return canon
        return min(name_set)

    # task_942: a keep-distinct row in value-aliases.tsv declares its alias column a
    # distinct fact that shares the canonical column's value by coincidence.
    # check_value_aliases proves the two are separate declarations and that the
    # row says why; here the alias side stops counting as a duplicate of a group
    # its canonical side is in. Nothing else exempts a value.
    aliases_tsv = os.path.join(os.path.dirname(os.path.abspath(baseline_path)), "value-aliases.tsv")
    if not os.path.isfile(aliases_tsv):
        emit("%s is absent -- its keep-distinct rows are what tell a coincidence from a "
             "duplicate, so this gate cannot classify a single group" % aliases_tsv)
        sys.exit(1)
    distinct = [(a, b) for _, a, b, disp, reason in _alias_rows(aliases_tsv)
                if disp == "keep-distinct" and len(reason) >= KEEP_DISTINCT_MIN_REASON]

    live, coincidental = {}, 0
    for val, keys in by_value.items():
        if val in EXEMPT_VALUES:
            continue
        decls = {}
        for k in keys:
            decls.setdefault(_shape(k), set()).add(k)
        if len(decls) < 2:
            continue
        explained = {_shape(b) for a, b in distinct
                     if a in env and b in env and env[a] == env[b] == val and _shape(a) != _shape(b)}
        residual = {i: n for i, n in decls.items() if i not in explained}
        if len(residual) > 1:
            live[val] = sorted(_rep(n) for n in residual.values())
        else:
            coincidental += 1

    # --- regeneration -----------------------------------------------------------
    if BUMP:
        header = list(DEFAULT_HEADER)
        if os.path.isfile(baseline_path):
            header = []
            with open(baseline_path, encoding="utf-8") as fh:
                for raw in fh:
                    raw = raw.rstrip("\n")
                    if raw.startswith("#!"):
                        continue
                    if raw.startswith("#") or not raw.strip():
                        header.append(raw)
                    else:
                        break
        rows = []
        for val in sorted(live, key=lambda v: (-len(live[v]), v)):
            rows.append("%s\t%d\t%s" % (esc(val), len(live[val]), ",".join(live[val])))
        with open(baseline_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(header) + "\n")
            fh.write("#!ceiling\t%d\n" % len(live))
            fh.write("\n".join(rows) + "\n")
        emit("LEDGER REGENERATED from the live resolver: %d groups, ceiling %d (MIOS_VALUE_DUP_BASELINE_BUMP=1)" % (len(live), len(live)))
        emit("review the diff -- every row added here is a duplicate this gate will stop reporting")
        sys.exit(0)

    # --- read the ledger --------------------------------------------------------
    ceiling = None
    base = {}
    try:
        fh = open(baseline_path, encoding="utf-8")
    except OSError as exc:
        emit("ratchet ledger unreadable: %s" % exc)
        sys.exit(1)
    with fh:
        for lineno, raw in enumerate(fh, 1):
            raw = raw.rstrip("\n")
            if raw.startswith("#!ceiling\t"):
                try:
                    ceiling = int(raw.split("\t", 1)[1].strip())
                except ValueError:
                    emit("ledger line %d: malformed #!ceiling directive" % lineno)
                    sys.exit(1)
                continue
            # Column 0 only: lstrip() here would swallow a data row whose VALUE
            # begins with whitespace and then a "#".
            if not raw.strip() or raw.startswith("#"):
                continue
            parts = raw.split("\t")
            if len(parts) != 3:
                emit("ledger line %d: expected 3 tab-separated fields, found %d" % (lineno, len(parts)))
                sys.exit(1)
            try:
                declared = int(parts[1])
            except ValueError:
                emit("ledger line %d: key_count field is not an integer" % lineno)
                sys.exit(1)
            keys = [k for k in parts[2].split(",") if k]
            if declared != len(keys):
                emit("ledger line %d: key_count %d disagrees with the %d keys listed" % (lineno, declared, len(keys)))
                sys.exit(1)
            base[unesc(parts[0])] = sorted(keys)

    bad = 0
    CAP = 15

    # --- new groups: a value that duplicates and is not on the ledger ------------
    new_groups = sorted(v for v in live if v not in base)
    if new_groups:
        bad += 1
        for val in new_groups[:CAP]:
            emit("NEW duplicate-value group, not on the ratchet ledger: %r is declared by %s "
                 "-- collapse it to one declaration, or, only if they are distinct facts, "
                 "register the pair keep-distinct with its reason in value-aliases.tsv"
                 % (val, ", ".join(live[val])))
        if len(new_groups) > CAP:
            emit("... and %d further new groups" % (len(new_groups) - CAP))

    # --- growth: a NEW declaration joining a group the ledger already tolerates --
    # Compared by declaration, not spelling: a ledger row names each declaration
    # once (its canonical), so a further alias of a recorded declaration is not
    # growth, and a recorded name that now traces elsewhere is.
    grown = []
    shrunk = []
    for val in sorted(live):
        if val not in base:
            continue
        have = {_shape(k): k for k in live[val]}
        had = {_shape(k): k for k in base[val]}
        added = sorted(have[i] for i in set(have) - set(had))
        removed = sorted(had[i] for i in set(had) - set(have))
        if added:
            grown.append((val, added))
        if removed:
            shrunk.append((val, removed))

    if grown:
        bad += 1
        for val, added in grown[:CAP]:
            emit("group %r GREW: %s now also declare it" % (val, ", ".join(added)))
        if len(grown) > CAP:
            emit("... and %d further grown groups" % (len(grown) - CAP))

    # --- shrinkage / disappearance: the ledger is stale and must be tightened ----
    gone = sorted(v for v in base if v not in live)
    if gone or shrunk:
        bad += 1
        for val in gone[:CAP]:
            emit("ledger records a group for %r that no longer exists -- tighten the ledger" % val)
        for val, removed in shrunk[:CAP]:
            emit("group %r SHRANK: %s no longer resolve to it -- tighten the ledger" % (val, ", ".join(removed)))
        if len(gone) + len(shrunk) > CAP:
            emit("... and %d further stale ledger rows" % (len(gone) + len(shrunk) - CAP))

    # --- the ceiling ------------------------------------------------------------
    if ceiling is None:
        bad += 1
        emit("ratchet ledger carries no #!ceiling directive -- a ratchet without a ceiling is not a ratchet")
    elif len(live) > ceiling:
        bad += 1
        emit("duplicate-value group count %d EXCEEDS the ratchet ceiling %d -- collapse the new duplicate instead of raising the ceiling" % (len(live), ceiling))
    elif len(live) < ceiling:
        bad += 1
        emit("duplicate-value group count %d is BELOW the ratchet ceiling %d -- lower the ceiling to %d so the progress is locked in" % (len(live), ceiling, len(live)))

    if bad:
        emit("resolver emitted %d MIOS_* keys forming %d non-exempt duplicate-value groups "
             "(%d more explained by keep-distinct rows); ledger declares %s"
             % (len(env), len(live), coincidental, ceiling))
        sys.exit(1)

    sys.stdout.write("%d groups at ceiling %d; %d coincidental groups explained by "
                     "value-aliases.tsv keep-distinct rows\n" % (len(live), ceiling, coincidental))
    sys.exit(0)

def check_unwired_modules() -> int:
    """An agent-pipe module imported but never called by a non-test caller.

    Lifted out of its shell heredoc so it can be imported, linted and tested;
    inside one, a syntax error surfaces only when the check runs.
    """
    import os, sys, ast
    root = os.environ["MIOS_DRIFT_ROOT"]
    pipe = os.path.join(root, "usr/lib/mios/agent-pipe")
    if not os.path.isdir(pipe):
        if os.environ.get("MIOS_DRIFT_REQUIRE_TOOLS") == "1":
            sys.stderr.write(f"FAIL: agent-pipe directory missing at {pipe} (MIOS_DRIFT_REQUIRE_TOOLS=1)\n")
            sys.exit(1)
        sys.exit(0)  # nothing to check on a bare checkout

    import tomllib as _toml
    with open(os.path.join(root, "usr/share/mios/mios.toml"), "rb") as fh:
        _data = _toml.load(fh)
    ALLOW = set(_data.get("drift", {}).get("denylist", []))

    def is_test(path):
        b = os.path.basename(path)
        if b.startswith("test_") or b.endswith("_test.py"):
            return True
        segs = path.replace("\\", "/").split("/")
        return "tests" in segs or "test" in segs

    pipe_py = []
    for dp, _dn, files in os.walk(pipe):
        for f in files:
            if f.endswith(".py") and not is_test(os.path.join(dp, f)):
                pipe_py.append(os.path.join(dp, f))
    ref_py = list(pipe_py)
    for sub in ("usr/libexec/mios", "tools"):
        base = os.path.join(root, sub)
        if not os.path.isdir(base):
            continue
        for dp, _dn, files in os.walk(base):
            for f in files:
                if f.endswith(".py") and not is_test(os.path.join(dp, f)):
                    ref_py.append(os.path.join(dp, f))

    modules = sorted(f[:-3] for f in os.listdir(pipe)
                     if f.startswith("mios_") and f.endswith(".py")
                     and not is_test(os.path.join(pipe, f)))

    def parse(p):
        try:
            return ast.parse(open(p, encoding="utf-8").read())
        except Exception:
            return None

    pipe_trees = {p: parse(p) for p in pipe_py}
    ref_trees = {p: parse(p) for p in ref_py}

    def binds(tree, mod):
        """Names this tree binds for `mod`: (import-aliases, from-names, star?)."""
        al, fr, star = set(), set(), False
        if tree is None:
            return al, fr, star
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                for a in n.names:
                    if a.name == mod:
                        al.add(a.asname or a.name)
            elif isinstance(n, ast.ImportFrom):
                if n.module == mod and (n.level or 0) == 0:
                    for a in n.names:
                        if a.name == "*":
                            star = True
                        else:
                            fr.add(a.asname or a.name)
        return al, fr, star

    def uses(tree, names):
        """True if tree references a bound name. Imports bind via alias nodes, not
        ast.Name, so any ast.Name match is a genuine (non-import) reference."""
        if tree is None or not names:
            return False
        for n in ast.walk(tree):
            if isinstance(n, ast.Name) and n.id in names:
                return True
        return False

    dead = set()
    for mod in modules:
        mf = os.path.abspath(os.path.join(pipe, mod + ".py"))
        imported = False
        for p, t in pipe_trees.items():
            if os.path.abspath(p) == mf:
                continue
            al, fr, star = binds(t, mod)
            if al or fr or star:
                imported = True
                break
        if not imported:
            continue  # never imported by the core -> not the imported-but-dead class
        wired = False
        for p, t in ref_trees.items():
            if os.path.abspath(p) == mf:
                continue
            al, fr, star = binds(t, mod)
            if star:
                wired = True
                break
            if (al or fr) and uses(t, al | fr):
                wired = True
                break
        if not wired:
            dead.add(mod)

    new_dead = sorted(dead - ALLOW)   # NEW imported-but-dead module -> fail
    stale = sorted(ALLOW - dead)      # allowlisted but now wired/removed -> fail
    for m in new_dead:
        sys.stderr.write(f"    {m}: imported by agent-pipe but no real (non-test) call site "
                         "-- wire it (give it a caller) or add it to _UNWIRED_ALLOW with a register note\n")
    for m in stale:
        sys.stderr.write(f"    {m}: listed in _UNWIRED_ALLOW but now WIRED or removed "
                         "-- delete it from the allowlist (A1 register self-cleans)\n")
    sys.exit(1 if (new_dead or stale) else 0)

def check_header_integrity() -> int:
    """A header tagger must never consume line 1 (task_2248)."""
    import os, re, subprocess, sys

    root = os.environ.get("MIOS_DRIFT_ROOT", ".")
    # `except Exception: sys.exit(0)` reported SUCCESS when git could not
    # answer, so the gate claimed no absorbed shebang in files it never opened.
    try:
        proc = subprocess.run(["git", "ls-files", "-z"], cwd=root,
                              capture_output=True, check=True)
    except Exception as exc:
        print("header-integrity: cannot enumerate the tree (%s), so no file "
              "header was inspected" % exc, file=sys.stderr)
        sys.exit(1)
    rels = [p for p in proc.stdout.decode("utf-8", "replace").split(chr(0)) if p]
    if not rels:
        print("header-integrity: git listed no tracked file, so no file "
              "header was inspected", file=sys.stderr)
        sys.exit(1)

    ABSORBED_SHEBANG = re.compile(r"AI-hint:\s*!")
    ABSORBED_DIRECTIVE = re.compile(r"AI-hint:\s*(?:bash|sh|python3?|pwsh|zsh)?\s*MIOS_[A-Z_]+=")
    NUL = b"\x00"
    viol = []
    inspected = 0
    absent = 0
    for rel in rels:
        p = os.path.join(root, rel.replace("/", os.sep))
        if not os.path.isfile(p):
            absent += 1
            continue
        try:
            with open(p, "rb") as fh:
                raw = fh.read(4096)
        except OSError:
            continue
        if NUL in raw:
            continue
        try:
            head = raw.decode("utf-8").splitlines()[:5]
        except UnicodeDecodeError:
            continue
        inspected += 1
        for ln in head:
            if ABSORBED_SHEBANG.search(ln):
                viol.append("%s: the shebang was absorbed into the AI-hint -- the file "
                            "has no interpreter line any more" % rel)
                break
            if ABSORBED_DIRECTIVE.search(ln):
                viol.append("%s: a MIOS_* build directive was folded into the AI-hint "
                            "instead of standing on its own line" % rel)
                break
    if viol:
        viol.append("A header tagger must never consume line 1. Restore the shebang "
                    "and the directive, then re-tag.")
        print("\n".join(viol))
        sys.exit(1)
    # The `not rels` guard counts what git LISTED; each listed file that was not
    # on disk was then skipped in silence, so an empty worktree read nothing.
    if not inspected:
        print("header-integrity: git listed %d tracked file(s) but not one could be "
              "read (%d missing from the worktree), so no file header was inspected"
              % (len(rels), absent), file=sys.stderr)
        sys.exit(1)
    print("    %d file header(s) inspected for an absorbed shebang or directive"
          % inspected)
    sys.exit(0)

def check_drift_projection() -> int:
    """Lifted out of a shell heredoc so it can be imported and linted."""
    import sys
    import os
    import json
    import collections
    import io
    import contextlib

    class MockCursor:
        def __init__(self, db_store):
            self.db_store = db_store
            self.results = []
            self.index = 0

        def execute(self, query, params=None):
            query_upper = " ".join(query.upper().split())
            self.results = []
            self.index = 0
            if "INSERT INTO SYSTEM_CONFIG" in query_upper:
                pass
            elif "INSERT INTO PACKAGE_SET" in query_upper:
                pass
            elif "INSERT INTO BUILD_PHASE" in query_upper:
                pass
            elif "INSERT INTO CONFIG_KV" in query_upper:
                if len(params) == 1:
                    val_json = params[0]
                    scope = "verbs"
                    key = "_defaults"
                    layer = 0
                else:
                    scope, key, val_json, desc = params
                    layer = 0
                self.db_store["config_kv"][(scope, key, layer)] = {
                    "scope": scope,
                    "key": key,
                    "value": json.loads(val_json) if isinstance(val_json, str) else val_json,
                    "layer": layer
                }
            elif "INSERT INTO VERB" in query_upper:
                name, sig, desc, tier, perm, cmd, params_json, section, examples_json, model_name, hidden, aliases_json, conflict_group, parallel_limit, max_result_chars = params
                self.db_store["verb"][name] = {
                    "name": name,
                    "sig": sig,
                    "desc_default": desc,
                    "tier": tier,
                    "permission": perm,
                    "cmd": cmd,
                    "params": json.loads(params_json) if isinstance(params_json, str) else params_json,
                    "section": section,
                    "examples": json.loads(examples_json) if isinstance(examples_json, str) else examples_json,
                    "model_name": model_name,
                    "hidden": hidden,
                    "aliases": json.loads(aliases_json) if isinstance(aliases_json, str) else aliases_json,
                    "conflict_group": conflict_group,
                    "parallel_limit": parallel_limit,
                    "max_result_chars": max_result_chars
                }
            elif "TRUNCATE TABLE DOMAIN_VERB" in query_upper:
                self.db_store["domain_verb"] = []
            elif "INSERT INTO DOMAIN_VERB" in query_upper:
                domain, verb_name, description = params
                self.db_store["domain_verb"].append({
                    "domain": domain,
                    "verb_name": verb_name,
                    "description": description
                })
            elif "SELECT 1 FROM VERB WHERE NAME =" in query_upper:
                name = params[0]
                if name in self.db_store["verb"]:
                    self.results = [(1,)]
                else:
                    self.results = []
                self.index = 0
            elif "SELECT SCOPE, KEY, VALUE::TEXT, LAYER FROM CONFIG_KV" in query_upper:
                rows = []
                for (scope, key, layer), item in sorted(self.db_store["config_kv"].items()):
                    if layer == 0:
                        rows.append((scope, key, json.dumps(item["value"]), layer))
                self.results = rows
                self.index = 0
            elif "SELECT DOMAIN, DESCRIPTION, ARRAY_AGG(VERB_NAME" in query_upper or "SELECT DOMAIN, DESCRIPTION, ARRAY_AGG" in query_upper:
                by_domain = collections.defaultdict(list)
                descs = {}
                for item in self.db_store["domain_verb"]:
                    dom = item["domain"]
                    by_domain[dom].append(item["verb_name"])
                    descs[dom] = item["description"]

                rows = []
                for dom in sorted(by_domain.keys()):
                    rows.append((dom, descs[dom], sorted(by_domain[dom])))
                self.results = rows
                self.index = 0
            elif "SELECT VALUE FROM CONFIG_KV WHERE SCOPE = 'VERBS' AND KEY = '_DEFAULTS'" in query_upper:
                item = self.db_store["config_kv"].get(('verbs', '_defaults', 0))
                if item:
                    self.results = [(item["value"],)]
                else:
                    self.results = []
                self.index = 0
            elif "SELECT NAME, SIG, DESC_DEFAULT, TIER, PERMISSION, CMD, PARAMS" in query_upper:
                rows = []
                for name in sorted(self.db_store["verb"].keys()):
                    v = self.db_store["verb"][name]
                    rows.append((
                        v["name"], v["sig"], v["desc_default"], v["tier"], v["permission"], v["cmd"],
                        v["params"], v["section"], v["examples"], v["model_name"], v["hidden"],
                        v["aliases"], v["conflict_group"], v["parallel_limit"], v["max_result_chars"]
                    ))
                self.results = rows
                self.index = 0

        def fetchall(self):
            return self.results

        def fetchone(self):
            if self.index < len(self.results):
                r = self.results[self.index]
                self.index += 1
                return r
            return None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    class MockConnection:
        def __init__(self, db_store):
            self.db_store = db_store

        def cursor(self):
            return MockCursor(self.db_store)

        def commit(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    class MockPsycopgModule:
        def __init__(self, db_store):
            self.db_store = db_store

        def connect(self, *args, **kwargs):
            return MockConnection(self.db_store)

    def check_roundtrip(root):
        db_store = {
            "config_kv": {},
            "verb": {},
            "domain_verb": []
        }
        mock_psycopg = MockPsycopgModule(db_store)
        sys.modules["psycopg"] = mock_psycopg

        seed_path = os.path.join(root, "usr/libexec/mios/seed-db-config.py")
        os.environ["MIOS_TOML"] = os.path.join(root, "usr/share/mios/mios.toml")
        os.environ["MIOS_VENDOR_TOML"] = os.environ["MIOS_TOML"]

        seed_globals = {"__name__": "__main__", "psycopg": mock_psycopg, "__file__": seed_path}
        try:
            with open(seed_path, "r", encoding="utf-8") as f:
                exec(f.read(), seed_globals)
        except SystemExit as e:
            if e.code != 0:
                print(f"Seed script exited with code {e.code}")
                sys.exit(1)

        materialize_path = os.path.join(root, "usr/libexec/mios/materialize-config-toml.py")
        mat_globals = {"__name__": "__main__", "psycopg": mock_psycopg, "__file__": materialize_path}

        stdout_capture = io.StringIO()
        original_argv = sys.argv
        try:
            sys.argv = [materialize_path]
            with contextlib.redirect_stdout(stdout_capture):
                with open(materialize_path, "r", encoding="utf-8") as f:
                    exec(f.read(), mat_globals)
        except SystemExit as e:
            if e.code != 0:
                print(f"Materialize script exited with code {e.code}")
                sys.exit(1)
        finally:
            sys.argv = original_argv

        materialized_toml_str = stdout_capture.getvalue()

        import tomllib

        with open(os.environ["MIOS_TOML"], "rb") as f:
            orig_data = tomllib.load(f)

        try:
            mat_data = tomllib.loads(materialized_toml_str)
        except Exception as parse_err:
            print("Materialized TOML parsing failed!")
            lines = materialized_toml_str.splitlines()
            import re as _re
            _m = _re.search(r"at line (\d+)", str(parse_err))
            _n = int(_m.group(1)) if _m else getattr(parse_err, "lineno", None)
            _lo = max(0, (_n - 4)) if _n else 29
            _hi = (_n + 3) if _n else 70
            print("Lines %d-%d:" % (_lo + 1, _hi))
            for i, l in enumerate(lines[_lo:_hi]):
                print(f"{_lo+i+1:4d}: {l}")
            raise parse_err

        scopes = ["ports", "ai", "routing", "pgvector", "a2a", "mcp", "observability", "sandbox", "security", "agent_passport", "agent_pipe"]
        for scope in scopes:
            orig_scope = orig_data.get(scope, {})
            mat_scope = mat_data.get(scope, {})

            if scope == "routing":
                orig_keys = {k: v for k, v in orig_scope.items() if k not in ("domains", "nohc_allowlist")}
                mat_keys = {k: v for k, v in mat_scope.items() if k not in ("domains", "nohc_allowlist")}
            else:
                orig_keys = orig_scope
                mat_keys = mat_scope

            if orig_keys != mat_keys:
                print(f"Drift in scope [{scope}]:")
                print(f"  Expected: {orig_keys}")
                print(f"  Got:      {mat_keys}")
                sys.exit(1)

        orig_domains = orig_data.get("routing", {}).get("domains", {})
        mat_domains = mat_data.get("routing", {}).get("domains", {})
        orig_domains_norm = {
            dom: {
                "desc": val.get("desc", ""),
                "verbs": sorted(val.get("verbs", []))
            }
            for dom, val in orig_domains.items()
        }
        mat_domains_norm = {
            dom: {
                "desc": val.get("desc", ""),
                "verbs": sorted(val.get("verbs", []))
            }
            for dom, val in mat_domains.items()
        }
        if orig_domains_norm != mat_domains_norm:
            print("Drift in routing.domains:")
            print(f"  Expected: {orig_domains_norm}")
            print(f"  Got:      {mat_domains_norm}")
            sys.exit(1)

        orig_verbs = orig_data.get("verbs", {})
        mat_verbs = mat_data.get("verbs", {})

        if orig_verbs.get("_defaults") != mat_verbs.get("_defaults"):
            print("Drift in verbs._defaults:")
            print(f"  Expected: {orig_verbs.get('_defaults')}")
            print(f"  Got:      {mat_verbs.get('_defaults')}")
            sys.exit(1)

        supported_verb_fields = {
            "sig", "desc", "tier", "permission", "cmd", "params",
            "section", "examples", "model_name", "hidden", "aliases",
            "conflict_group", "parallel_limit", "max_result_chars"
        }

        for vname, orig_vcfg in orig_verbs.items():
            if vname == "_defaults":
                continue
            if vname not in mat_verbs:
                print(f"Verb '{vname}' missing in materialized output")
                sys.exit(1)

            mat_vcfg = mat_verbs[vname]
            orig_defaults = orig_verbs.get("_defaults", {})
            mat_defaults = mat_verbs.get("_defaults", {})

            orig_full = orig_defaults.copy()
            orig_full.update(orig_vcfg)

            mat_full = mat_defaults.copy()
            mat_full.update(mat_vcfg)

            for key in supported_verb_fields:
                orig_val = orig_full.get(key)
                mat_val = mat_full.get(key)

                if key in ("sig", "desc", "cmd", "section", "model_name", "conflict_group"):
                    if orig_val == "": orig_val = None
                    if mat_val == "": mat_val = None
                elif key in ("examples", "aliases"):
                    if orig_val == []: orig_val = None
                    if mat_val == []: mat_val = None
                elif key == "params":
                    if orig_val == {}: orig_val = None
                    if mat_val == {}: mat_val = None
                elif key == "hidden":
                    orig_val = bool(orig_val)
                    mat_val = bool(mat_val)
                elif key in ("parallel_limit", "max_result_chars"):
                    orig_val = int(orig_val or 0)
                    mat_val = int(mat_val or 0)

                if orig_val != mat_val:
                    print(f"Drift in verb '{vname}' field '{key}':")
                    print(f"  Expected: {orig_val}")
                    print(f"  Got:      {mat_val}")
                    sys.exit(1)

        sys.exit(0)

    if __name__ == "__main__":
        check_roundtrip(os.environ["MIOS_DRIFT_ROOT"])

def check_drift_build_catalog() -> int:
    """Lifted out of a shell heredoc so it can be imported and linted."""
    import sys
    import os
    import json
    import collections
    import io
    import contextlib

    class MockCursor:
        def __init__(self, db_store):
            self.db_store = db_store
            self.results = []
            self.index = 0

        def execute(self, query, params=None):
            query_upper = " ".join(query.upper().split())

            if "INSERT INTO SYSTEM_CONFIG" in query_upper:
                pass
            elif "INSERT INTO CONFIG_KV" in query_upper:
                pass
            elif "INSERT INTO VERB" in query_upper:
                pass
            elif "TRUNCATE TABLE DOMAIN_VERB" in query_upper:
                pass
            elif "INSERT INTO DOMAIN_VERB" in query_upper:
                pass
            elif "SELECT 1 FROM VERB" in query_upper:
                self.results = []
                self.index = 0
            elif "INSERT INTO PACKAGE_SET" in query_upper:
                name, section, pkgs_json, enable, layer, base_image_ref = params
                self.db_store["package_set"][name] = {
                    "name": name,
                    "section": section,
                    "pkgs": pkgs_json,
                    "enable": enable,
                    "layer": layer,
                    "base_image_ref": base_image_ref
                }
            elif "INSERT INTO BUILD_PHASE" in query_upper:
                if len(params) == 3:
                    ordinal, script, deps_json = params
                    stage = "container"
                else:
                    script = params[0]
                    ordinal = None
                    stage = "firstboot"
                    deps_json = "[]"
                self.db_store["build_phase"][script] = {
                    "ordinal": ordinal,
                    "script": script,
                    "stage": stage,
                    "deps": deps_json
                }
            elif "INSERT INTO DEBLOAT_POLICY" in query_upper:
                name, policy_type, rules_json = params
                self.db_store["debloat_policy"][name] = {
                    "name": name,
                    "policy_type": policy_type,
                    "rules": rules_json
                }
            elif "INSERT INTO DEBLOAT_PROFILE" in query_upper:
                self.db_store["debloat_profile"]["default"] = {
                    "name": "default",
                    "description": "Default debloat profile"
                }
            elif "INSERT INTO PRESET" in query_upper:
                features_json = params[0]
                self.db_store["preset"]["default"] = {
                    "name": "default",
                    "description": "Default preset",
                    "features": features_json,
                    "debloat_profile_name": "default"
                }
            elif "SELECT NAME, SECTION, PKGS, ENABLE, LAYER, BASE_IMAGE_REF FROM PACKAGE_SET" in query_upper:
                rows = []
                for name in sorted(self.db_store["package_set"].keys()):
                    p = self.db_store["package_set"][name]
                    rows.append({
                        "name": p["name"],
                        "section": p["section"],
                        "pkgs": p["pkgs"],
                        "enable": p["enable"],
                        "layer": p["layer"],
                        "base_image_ref": p["base_image_ref"]
                    })
                self.results = rows
                self.index = 0
            elif "SELECT ORDINAL, SCRIPT, STAGE, DEPS FROM BUILD_PHASE" in query_upper:
                rows = []
                def sort_key(item):
                    o = item["ordinal"]
                    return (item["stage"], o if o is not None else 999999, item["script"])
                for script in sorted(self.db_store["build_phase"].keys()):
                    p = self.db_store["build_phase"][script]
                    rows.append(p)
                rows.sort(key=sort_key)
                self.results = [{
                    "ordinal": r["ordinal"],
                    "script": r["script"],
                    "stage": r["stage"],
                    "deps": r["deps"]
                } for r in rows]
                self.index = 0
            elif "SELECT NAME, POLICY_TYPE, RULES FROM DEBLOAT_POLICY" in query_upper:
                rows = []
                for name in sorted(self.db_store["debloat_policy"].keys()):
                    p = self.db_store["debloat_policy"][name]
                    rows.append({
                        "name": p["name"],
                        "policy_type": p["policy_type"],
                        "rules": p["rules"]
                    })
                self.results = rows
                self.index = 0
            elif "SELECT NAME, DESCRIPTION FROM DEBLOAT_PROFILE" in query_upper:
                rows = []
                for name in sorted(self.db_store["debloat_profile"].keys()):
                    p = self.db_store["debloat_profile"][name]
                    rows.append({
                        "name": p["name"],
                        "description": p["description"]
                    })
                self.results = rows
                self.index = 0
            elif "SELECT NAME, DESCRIPTION, FEATURES, DEBLOAT_PROFILE_NAME FROM PRESET" in query_upper:
                rows = []
                for name in sorted(self.db_store["preset"].keys()):
                    p = self.db_store["preset"][name]
                    rows.append({
                        "name": p["name"],
                        "description": p["description"],
                        "features": p["features"],
                        "debloat_profile_name": p["debloat_profile_name"]
                    })
                self.results = rows
                self.index = 0

        def fetchall(self):
            return self.results

        def fetchone(self):
            if self.index < len(self.results):
                r = self.results[self.index]
                self.index += 1
                return r
            return None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    class MockConnection:
        def __init__(self, db_store):
            self.db_store = db_store

        def cursor(self, row_factory=None):
            return MockCursor(self.db_store)

        def commit(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    class MockPsycopgModule:
        def __init__(self, db_store):
            self.db_store = db_store

        def connect(self, *args, **kwargs):
            return MockConnection(self.db_store)

    def check_roundtrip(root):
        db_store = {
            "package_set": {},
            "build_phase": {},
            "debloat_policy": {},
            "debloat_profile": {},
            "preset": {}
        }
        mock_psycopg = MockPsycopgModule(db_store)
        mock_psycopg.__path__ = []
        class DictRowMock:
            pass
        mock_psycopg.rows = DictRowMock()
        mock_psycopg.rows.dict_row = DictRowMock

        sys.modules["psycopg"] = mock_psycopg
        sys.modules["psycopg.rows"] = mock_psycopg.rows

        seed_path = os.path.join(root, "usr/libexec/mios/seed-db-config.py")
        os.environ["MIOS_TOML"] = os.path.join(root, "usr/share/mios/mios.toml")
        os.environ["MIOS_VENDOR_TOML"] = os.environ["MIOS_TOML"]

        seed_globals = {"__name__": "__main__", "psycopg": mock_psycopg, "__file__": seed_path}
        try:
            with open(seed_path, "r", encoding="utf-8") as f:
                exec(f.read(), seed_globals)
        except SystemExit as e:
            if e.code != 0:
                print(f"Seed script exited with code {e.code}")
                sys.exit(1)

        materialize_path = os.path.join(root, "usr/libexec/mios/materialize-build-ctx.py")
        temp_ctx_dir = "/tmp/mios-drift-ctx-test"
        os.makedirs(temp_ctx_dir, exist_ok=True)
        os.environ["MIOS_BUILD_CTX"] = temp_ctx_dir

        mat_globals = {"__name__": "__main__", "psycopg": mock_psycopg, "__file__": materialize_path}
        try:
            with open(materialize_path, "r", encoding="utf-8") as f:
                exec(f.read(), mat_globals)
        except SystemExit as e:
            if e.code != 0:
                print(f"Materialize script exited with code {e.code}")
                sys.exit(1)

        import tomllib

        with open(os.environ["MIOS_TOML"], "rb") as f:
            toml_data = tomllib.load(f)

        with open(os.path.join(temp_ctx_dir, "package_sets.json"), "r", encoding="utf-8") as f:
            mat_sets = json.load(f)

        orig_packages = toml_data.get("packages", {})
        for sec_name, sec_cfg in orig_packages.items():
            if sec_name == "sections" or not isinstance(sec_cfg, dict) or "pkgs" not in sec_cfg:
                continue
            mat_item = next((x for x in mat_sets if x["name"] == sec_name), None)
            if not mat_item:
                print(f"Drift: Package set '{sec_name}' missing in materialized output")
                sys.exit(1)
            orig_pkgs = sec_cfg.get("pkgs", [])
            mat_pkgs = mat_item["pkgs"]
            if orig_pkgs != mat_pkgs:
                print(f"Drift in package set '{sec_name}':")
                print(f"  Expected: {orig_pkgs}")
                print(f"  Got:      {mat_pkgs}")
                sys.exit(1)

            orig_enable = sec_cfg.get("enable", True)
            orig_layer = sec_cfg.get("layer", 0)
            orig_base_ref = sec_cfg.get("base_image_ref", "")
            orig_section = sec_cfg.get("section", "Misc")

            if (mat_item.get("enable", True) != orig_enable or
                mat_item.get("layer", 0) != orig_layer or
                mat_item.get("base_image_ref", "") != orig_base_ref or
                mat_item.get("section", "Misc") != orig_section):
                print(f"Drift in package set '{sec_name}' metadata:")
                print(f"  Expected: enable={orig_enable}, layer={orig_layer}, base={orig_base_ref}, section={orig_section}")
                print(f"  Got:      enable={mat_item.get('enable')}, layer={mat_item.get('layer')}, base={mat_item.get('base_image_ref')}, section={mat_item.get('section')}")
                sys.exit(1)

        with open(os.path.join(temp_ctx_dir, "build_phases.json"), "r", encoding="utf-8") as f:
            mat_phases = json.load(f)

        automation_dir = os.path.join(root, "automation")
        import re
        scripts = sorted([f for f in os.listdir(automation_dir) if re.match(r"^\d{2}-.*\.sh$", f)])

        prev_script = None
        for s in scripts:
            ordinal = int(s.split("-", 1)[0])
            expected_deps = [prev_script] if prev_script else []
            mat_item = next((x for x in mat_phases if x["script"] == s), None)
            if not mat_item:
                print(f"Drift: Build phase script '{s}' missing in materialized output")
                sys.exit(1)
            if mat_item["ordinal"] != ordinal or mat_item["deps"] != expected_deps or mat_item["stage"] != "container":
                print(f"Drift in build phase script '{s}':")
                print(f"  Expected: ordinal={ordinal}, stage=container, deps={expected_deps}")
                print(f"  Got:      ordinal={mat_item['ordinal']}, stage={mat_item['stage']}, deps={mat_item['deps']}")
                sys.exit(1)
            prev_script = s

        bootstrap_dir = os.path.abspath(os.path.join(root, "..", "mios-bootstrap", "src", "autounattend"))
        debloat_json_path = os.path.join(bootstrap_dir, "mios-debloat.json")
        features_txt_path = os.path.join(bootstrap_dir, "mios-xbox-features.txt")

        if os.path.isfile(debloat_json_path) or os.path.isfile(features_txt_path):
            with open(os.path.join(temp_ctx_dir, "debloat_profiles.json"), "r", encoding="utf-8") as f:
                mat_debloat = json.load(f)

            if os.path.isfile(debloat_json_path):
                with open(debloat_json_path, "r", encoding="utf-8") as f:
                    orig_debloat = json.load(f)
                for k, val in orig_debloat.items():
                    if k == "_comment" or not isinstance(val, list):
                        continue
                    mat_policy = next((x for x in mat_debloat["policies"] if x["name"] == k), None)
                    if not mat_policy:
                        print(f"Drift: Debloat policy '{k}' missing in materialized output")
                        sys.exit(1)
                    if mat_policy["rules"] != val:
                        print(f"Drift in debloat policy '{k}'")
                        sys.exit(1)

            if os.path.isfile(features_txt_path):
                with open(features_txt_path, "r", encoding="utf-8") as f:
                    orig_features = [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]
                mat_preset = next((x for x in mat_debloat["presets"] if x["name"] == "default"), None)
                if not mat_preset:
                    print("Drift: Default preset missing in materialized output")
                    sys.exit(1)
                if mat_preset["features"] != orig_features:
                    print("Drift in preset features")
                    sys.exit(1)
                if mat_preset.get("debloat_profile_name") != "default":
                    print("Drift: Default preset debloat_profile_name is not 'default'")
                    sys.exit(1)

            mat_profile = next((x for x in mat_debloat["profiles"] if x["name"] == "default"), None)
            if not mat_profile:
                print("Drift: Default debloat profile missing in materialized output")
                sys.exit(1)
            if mat_profile.get("description") != "Default debloat profile":
                print("Drift: Default debloat profile description does not match")
                sys.exit(1)

        sys.exit(0)

    if __name__ == "__main__":
        check_roundtrip(os.environ["MIOS_DRIFT_ROOT"])

def check_structured() -> int:
    """A [nodes.local-*] lane with no server, or an ai/v1 manifest that does not resolve.

    Lifted out of its shell heredoc so it can be imported, linted and tested;
    inside one, a syntax error surfaces only when the check runs.
    """
    import os, sys, re, json
    root = os.environ["MIOS_DRIFT_ROOT"]
    viol = []

    import tomllib as _toml

    toml_path = os.path.join(root, "usr/share/mios/mios.toml")
    if not _scan(root, toml_path):
        return 0
    if os.path.isfile(toml_path):
        with open(toml_path, "rb") as fh:
            data = _toml.load(fh)
        nodes = data.get("nodes", {}) or {}
        served = set()
        for ud in ("usr/share/containers/systemd", "usr/lib/systemd/system",
                   "etc/containers/systemd"):
            base = os.path.join(root, ud)
            if not _scan(root, base):
                continue
            for dirpath, _dn, files in os.walk(base):
                for fn in files:
                    if not fn.endswith((".container", ".service")):
                        continue
                    try:
                        txt = open(os.path.join(dirpath, fn), encoding="utf-8",
                                   errors="ignore").read()
                    except OSError:
                        continue
                    for m in re.findall(r":(\d{4,5})\b", txt):
                        served.add(m)
                    for m in re.findall(r"(?:--port[= ]|PublishPort[= ])(\d{4,5})", txt):
                        served.add(m)
        for name, cfg in nodes.items():
            if not isinstance(cfg, dict):
                continue
            ep = (cfg.get("endpoint") or "").strip()
            if not ep:
                continue  # empty endpoint = inert node, skipped by the loader
            m = re.search(r"://(?:localhost|127\.0\.0\.1|host\.containers\.internal):(\d{4,5})", ep)
            if not m:
                continue  # remote / non-local endpoint -- operator overlay, unverifiable
            port = m.group(1)
            if port not in served:
                viol.append(f"[nodes.{name}] endpoint {ep} -> localhost:{port} is served by NO shipped unit "
                            f"(dangling lane; served ports: {sorted(served)})")

        obs = data.get("observability", {}) or {}
        if "surface_default" not in obs:
            viol.append("[observability] surface_default is missing")
        elif obs.get("surface_default") not in ("clean", "inline"):
            viol.append(f"[observability] surface_default '{obs.get('surface_default')}' must be 'clean' or 'inline'")

        channels = obs.get("channels", {}) or {}
        req_channels = {"thinking", "plan", "tool_call", "tool_result", "source", "content"}
        for rc in req_channels:
            if rc not in channels:
                viol.append(f"[observability.channels] key '{rc}' is missing")

        lanes = data.get("lanes", {}) or {}
        for lname in ("light", "sglang", "vllm"):
            if lname not in lanes:
                viol.append(f"[lanes.{lname}] section is missing")
            else:
                lcfg = lanes[lname] or {}
                for k in ("stream_thinking", "tool_call_parser", "reasoning_parser", "constrained_tools"):
                    if k not in lcfg:
                        viol.append(f"[lanes.{lname}].{k} is missing")

        ap = data.get("agent_pipe", {}) or {}
        for k in ("tool_loop_limit", "reflexion_limit", "reflexion_enable"):
            if k not in ap:
                viol.append(f"[agent_pipe].{k} is missing")

    v1 = os.path.join(root, "usr/share/mios/ai/v1")
    if os.path.isdir(v1):
        for fn in sorted(os.listdir(v1)):
            if not fn.endswith(".json"):
                continue
            p = os.path.join(v1, fn)
            try:
                doc = json.load(open(p, encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                viol.append(f"ai/v1/{fn} does not parse as JSON: {e}")
                continue
            if fn == "tools.json":
                for e in doc.get("data", []):
                    if not isinstance(e, dict):
                        continue
                    for key in ("chat_completions", "responses", "schema_output"):
                        ref = e.get(key)
                        if isinstance(ref, str) and ref.startswith("/usr/"):
                            if not os.path.exists(os.path.join(root, ref.lstrip("/"))):
                                viol.append(f"tools.json: {e.get('name')!r} {key} -> {ref} (missing on disk)")

    for v in viol:
        sys.stderr.write(f"    {v}\n")
    sys.exit(1 if viol else 0)

def check_negative_test_coverage() -> int:
    """Lifted from a shell heredoc so it can be imported, linted and tested.

    Inside a heredoc a syntax error surfaces only when the check runs.
    """
    import os, sys, re

    root = os.environ["MIOS_DRIFT_ROOT"]
    harness_path = os.path.join(root, "tests/drift-gate-negatives.sh")

    _rc = _absent(root, harness_path)
    if _rc is not None:
        sys.exit(_rc)

    with open(harness_path, "r", encoding="utf-8", errors="ignore") as f:
        harness_content = f.read()

    required_checks = [
        "check_version_ssot",
        "check_resolver_twin_equivalence",
        "check_cli_eval_safety",
        "check_shellcheck",
        "check_names_registry",
        "check_root_toml_subset",
        "check_toml_projection",
        "check_curl_retry",
        "check_nested_podman_caps",
        "check_bake_budget",
        "check_module_test_coverage",
        "check_router_parity",
        "check_council_gate_ssot",
        "check_agent_pipe_budgets",
        "check_bake_plan",
        "check_containerfile_pinned_clones",
        "check_firstboot_tier",
        "check_bound_image_store",
        "check_rechunk_budget",
        "check_gate_registry",
        "check_test_hermeticity",
        "check_no_mkdir_in_var",
        "check_quadlet_privilege",
        "check_firstboot_degrade_open",
        "check_firstboot_provisioners",
        "check_schema_consumers",
        "check_tasks_status_parity",
        "check_agy_tasks",
        "check_task_ids",
        "check_mios_toml_integrity",
        "check_privileged_quadlets_minimal",
        "check_container_names",
        "check_service_urls",
        "check_ports_bound",
        "check_blade_coverage",
        "check_blade_karg",
        "check_role_ssot",
        "check_port_fallbacks",
        "check_node_pool",
        "check_metal_vs_hosted",
        "check_unit_projection",
        "check_ssot_consumer_keys",
        "check_fleet_safety",
        "check_adr_index",
        "check_ssot_lint_equivalence",
        "check_oci_archive_path",
        "check_replaceme_mount_substitution",
        "check_kickstart_shell_syntax",
        "check_offline_install_invariant",
        "check_installer_family_roles",
        "check_bib_configs_projection",
        "check_repo_partition_label_ssot",
        "check_bib_single_config_invariant",
        "check_artifact_recipes",
        "check_build_artifacts_output_dir",
        "check_win11_vm_template_xml",
        "check_ipa_enroll_projection",
        "check_bootc_install_projection",
        "check_uki_cmdline_projection",
        "check_composefs_projection",
        "check_cockpit_projection",
        "check_chrony_ptp_dropin",
        "check_chrony_projection",
        "check_nut_projection",
        "check_renderer_gate_coverage",
    ]

    test_fns = re.findall(r'^\s*(test_[a-z0-9_]+)\(\)\s*\{', harness_content, re.MULTILINE)

    bad = []
    if len(test_fns) < len(required_checks):
        bad.append(f"Negative test suite count ({len(test_fns)}) is less than required law gates count ({len(required_checks)})")

    for chk in required_checks:
        if chk not in harness_content:
            bad.append(f"Required law/security check '{chk}' has no negative test in tests/drift-gate-negatives.sh")

    if bad:
        for b in bad:
            sys.stderr.write(f"    [negatives-coverage-drift] {b}\n")
        sys.exit(1)

    sys.exit(0)

def check_bake_plan_integrity() -> int:
    """Compatibility entry; native Rust owns the bake-plan integrity policy."""
    import subprocess
    root = os.environ["MIOS_DRIFT_ROOT"]
    return subprocess.run(
        ["bash", os.path.join(root, "automation/98-drift-checks.sh"), "check_bake_plan_integrity"],
        env=dict(os.environ, MIOS_DRIFT_CHECK_ROOT=root), check=False,
    ).returncode

def check_globals_image_parity() -> int:
    """Lifted from a shell heredoc so it can be imported, linted and tested.

    Inside a heredoc a syntax error surfaces only when the check runs.
    """
    import os, sys, re
    root = os.environ["MIOS_DRIFT_ROOT"]
    import tomllib as _toml
    toml = os.path.join(root, "usr/share/mios/mios.toml")
    _rc = _absent(root, toml)
    if _rc is not None:
        sys.exit(_rc)
    with open(toml, "rb") as fh:
        img = (_toml.load(fh).get("image", {}) or {})

    expected_name = img.get("name", "ghcr.io/mios-dev/mios")
    expected_base = img.get("base", "ghcr.io/ublue-os/ucore-hci:stable-nvidia")
    expected_bib = img.get("bib", "quay.io/centos-bootc/bootc-image-builder:latest")

    bad = []
    sh = os.path.join(root, "automation/lib/globals.sh")
    if os.path.isfile(sh):
        with open(sh, encoding="utf-8") as fh:
            content = fh.read()

            m = re.search(r'MIOS_IMAGE_NAME:=([^}]+)\}', content)
            if m:
                got = m.group(1).strip('"\' ')
                if got != expected_name:
                    bad.append(f"globals.sh default MIOS_IMAGE_NAME={got} != mios.toml [image].name={expected_name}")
            else:
                bad.append("globals.sh is missing default MIOS_IMAGE_NAME definition")

            m = re.search(r'MIOS_BASE_IMAGE:=([^}]+)\}', content)
            if m:
                got = m.group(1).strip('"\' ')
                if got != expected_base:
                    bad.append(f"globals.sh default MIOS_BASE_IMAGE={got} != mios.toml [image].base={expected_base}")
            else:
                bad.append("globals.sh is missing default MIOS_BASE_IMAGE definition")

            m = re.search(r'MIOS_BIB_IMAGE:=([^}]+)\}', content)
            if m:
                got = m.group(1).strip('"\' ')
                if got != expected_bib:
                    bad.append(f"globals.sh default MIOS_BIB_IMAGE={got} != mios.toml [image].bib={expected_bib}")
            else:
                bad.append("globals.sh is missing default MIOS_BIB_IMAGE definition")

    ps1 = os.path.join(root, "automation/lib/globals.ps1")
    if os.path.isfile(ps1):
        with open(ps1, encoding="utf-8") as fh:
            content = fh.read()

            m = re.search(r'\$defaultImageName\s*=\s*([^#\r\n]+)', content)
            if m:
                got = m.group(1).strip('"\' ')
                if got != expected_name:
                    bad.append(f"globals.ps1 defaultImageName={got} != mios.toml [image].name={expected_name}")
            else:
                bad.append("globals.ps1 is missing $defaultImageName definition")

            m = re.search(r'MIOS_BASE_IMAGE[^\r\n]+else\s*\{\s*([^}]+)\}', content)
            if m:
                got = m.group(1).strip('"\' ')
                if got != expected_base:
                    bad.append(f"globals.ps1 default MIOS_BASE_IMAGE={got} != mios.toml [image].base={expected_base}")
            else:
                bad.append("globals.ps1 is missing default MIOS_BASE_IMAGE definition")

            m = re.search(r'MIOS_BIB_IMAGE[^\r\n]+else\s*\{\s*([^}]+)\}', content)
            if m:
                got = m.group(1).strip('"\' ')
                if got != expected_bib:
                    bad.append(f"globals.ps1 default MIOS_BIB_IMAGE={got} != mios.toml [image].bib={expected_bib}")
            else:
                bad.append("globals.ps1 is missing default MIOS_BIB_IMAGE definition")

    for b in bad:
        sys.stderr.write(f"    {b}\n")
    sys.exit(1 if bad else 0)

def check_no_bare_port_literals() -> int:
    """Lifted from a shell heredoc so it can be imported, linted and tested.

    Inside a heredoc a syntax error surfaces only when the check runs.
    """
    import os, sys, re, ast

    root = os.environ.get("MIOS_DRIFT_ROOT", ".")
    banned_ports = ["11450", "11441", "11440", "11451", "11434", "11435"]
    scan_dirs = [
        os.path.join(root, "usr/lib/mios/agent-pipe"),
        os.path.join(root, "usr/libexec/mios"),
        os.path.join(root, "usr/bin")
    ]

    class DocstringCollector(ast.NodeVisitor):
        def __init__(self):
            self.docstring_nodes = set()
        def check_body(self, body):
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                if isinstance(body[0].value.value, str):
                    self.docstring_nodes.add(body[0].value)
        def visit_Module(self, node):
            self.check_body(node.body)
            self.generic_visit(node)
        def visit_FunctionDef(self, node):
            self.check_body(node.body)
            self.generic_visit(node)
        def visit_AsyncFunctionDef(self, node):
            self.check_body(node.body)
            self.generic_visit(node)
        def visit_ClassDef(self, node):
            self.check_body(node.body)
            self.generic_visit(node)

    violations = []
    scanned = 0
    missing = [d for d in scan_dirs if not os.path.isdir(d)]
    if missing:
        for d in missing:
            sys.stderr.write("    %s is absent, so no execution path there was scanned\n"
                             % os.path.relpath(d, root).replace(os.sep, "/"))
        return 1
    for d in scan_dirs:
        for r, ds, fs in os.walk(d):
            for f in fs:
                if not f.endswith((".py", ".sh", ".ps1")) or "test_" in f:
                    continue
                if f in ["Setup-MiOSLanPortProxy.ps1", "Heal-MiOSLocalhostForwarding.ps1", "Setup-MiOSLanPortProxy.ps1.bom-bak", "mios-doctor", "net_segmentation.py", "selinux_policy.py", "model_matrix_alloc.py", "editor_config_gen.py", "fastfetch_gen.py", "gnome_extension.py", "status_bar.py"]:
                    continue
                path = os.path.join(r, f)
                try:
                    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                        content = fh.read()
                    scanned += 1

                    if f.endswith(".py"):
                        try:
                            tree = ast.parse(content)
                            collector = DocstringCollector()
                            collector.visit(tree)

                            for node in ast.walk(tree):
                                if isinstance(node, ast.Constant):
                                    if node in collector.docstring_nodes:
                                        continue
                                    val = str(node.value)
                                    for port in banned_ports:
                                        if port in val:
                                            violations.append(f"{f}:{getattr(node, 'lineno', '?')} contains banned port '{port}' in constant '{val}'")
                        except Exception as e:
                            for line_no, line in enumerate(content.splitlines(), 1):
                                stripped = line.strip()
                                if stripped.startswith(("#", "'''", '"""')):
                                    continue
                                code_part = line.split("#", 1)[0]
                                for port in banned_ports:
                                    if port in code_part:
                                        violations.append(f"{f}:{line_no} contains banned port '{port}' (fallback)")
                    else:
                        for line_no, line in enumerate(content.splitlines(), 1):
                            stripped = line.strip()
                            if stripped.startswith(("#", "//", "Write-Host", "echo", "help", "usage")):
                                continue
                            # Only "#" starts a comment in sh and PowerShell. Splitting on
                            # "//" as well truncated every line at the scheme separator of a
                            # URL, so a retired port was invisible in http://host:PORT/... --
                            # the one form these ports actually take. Verified: PORT=11434 was
                            # reported, the identical port inside a URL was not.
                            code_part = line.split("#", 1)[0]
                            for port in banned_ports:
                                if port in code_part:
                                    violations.append(f"{f}:{line_no} contains banned port '{port}'")
                except OSError:
                    pass

    if scanned < 100:
        sys.stderr.write("    only %d execution-path file(s) scanned -- the corpus is "
                         "wrong, so an empty result is not a pass\n" % scanned)
        sys.exit(1)

    if violations:
        for v in sorted(set(violations)):
            sys.stderr.write(f"    {v}\n")
        sys.exit(1)
    print("%d execution-path file(s) scanned for %d retired port(s)"
          % (scanned, len(banned_ports)))
    sys.exit(0)

def check_verb_stub_backends() -> int:
    """Lifted from a shell heredoc so it can be imported, linted and tested.

    Inside a heredoc a syntax error surfaces only when the check runs.
    """
    import os, sys, glob, re
    import tomllib

    root = os.environ["MIOS_DRIFT_ROOT"]
    toml_path = os.path.join(root, "usr/share/mios/mios.toml")

    if not os.path.isfile(toml_path):
        sys.stderr.write("    SSOT mios.toml missing\n")
        sys.exit(1)

    with open(toml_path, "rb") as f:
        data = tomllib.load(f)

    verbs = data.get("verbs", {})
    violations = []

    def check_script_body(filepath):
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
        except Exception as e:
            return f"Cannot read file {filepath}: {e}"

        code_lines = []
        for line in lines:
            l = line.strip()
            if not l or l.startswith("#"):
                continue
            if re.match(r'^(echo|printf|set\s+-|exit\s+[0-9]+|true|return\s+[0-9]+|export\s+[A-Z_]+=|usage\(\)\s*\{|:\s*;\s*|\}\s*;\s*)$', l):
                continue
            code_lines.append(l)

        if len(code_lines) == 0:
            return "Script body is a stub (produces no side effects)"
        return None

    REGISTERED_STUBS = set()

    for sdir in [os.path.join(root, "usr/libexec/mios"), os.path.join(root, "installation")]:
        if os.path.isdir(sdir):
            for path in glob.glob(os.path.join(sdir, "**/*"), recursive=True):
                if os.path.isfile(path) and (path.endswith(".sh") or path.endswith(".ps1") or "." not in os.path.basename(path)):
                    rel = os.path.relpath(path, root).replace("\\", "/")
                    res = check_script_body(path)
                    if res and rel not in REGISTERED_STUBS:
                        violations.append(f"{rel}: {res}")

    def walk_verbs(prefix, d):
        for k, v in d.items():
            if k == "_defaults":
                continue
            full_name = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                cmd = v.get("cmd") or v.get("exec")
                if cmd:
                    tokens = cmd.strip().split()
                    first = tokens[0] if tokens else ""
                    if first.startswith("/usr/libexec/mios/") or first.startswith("/installation/"):
                        rel_path = first.lstrip("/")
                        full = os.path.join(root, rel_path)
                        if not os.path.exists(full):
                            violations.append(f"Verb {full_name} backend script missing: {rel_path}")
                elif any(isinstance(val, dict) for val in v.values()):
                    walk_verbs(full_name, v)

    walk_verbs("", verbs)

    if violations:
        for v in violations:
            sys.stderr.write(f"    {v}\n")
        sys.exit(1)

    sys.exit(0)

def check_cephfs_ssot() -> int:
    """Lifted from a shell heredoc so it can be imported, linted and tested.

    Inside a heredoc a syntax error surfaces only when the check runs.
    """
    import os, sys
    root = os.environ["MIOS_DRIFT_ROOT"]
    viol = []

    import tomllib as _toml

    toml_path = os.path.join(root, "usr/share/mios/mios.toml")
    if not os.path.isfile(toml_path):
        viol.append("usr/share/mios/mios.toml is missing, so [storage.cephfs] was never read")
    else:
        with open(toml_path, "rb") as fh:
            data = _toml.load(fh)
        cephfs = data.get("storage", {}).get("cephfs", {}) or {}
        enable = cephfs.get("enable", False)

        if enable:
            monitors = cephfs.get("monitors", [])
            if not monitors or monitors == ["127.0.0.1:6789"]:
                viol.append("[storage.cephfs].monitors must be set to actual monitor IPs when enable=true")

            cache_override = cephfs.get("xdg_cache_home_override", "")
            hostnames = [m.split(":")[0] for m in monitors]
            if ("ceph" in cache_override.lower() or
                    "/tenants/" in cache_override or
                    cache_override.startswith("/home/") or
                    any(h in cache_override for h in hostnames if h)):
                viol.append("[storage.cephfs].xdg_cache_home_override must be local tmpfs, NEVER CephFS (MDS storm hazard)")

            hot_pool = cephfs.get("data_pool_hot", "")
            bulk_pool = cephfs.get("data_pool_bulk", "")
            if hot_pool and bulk_pool and hot_pool == bulk_pool:
                viol.append("[storage.cephfs] data_pool_hot and data_pool_bulk must be distinct pools for tiering")

            prov_script = cephfs.get("provision_script", "")
            if prov_script:
                rel_path = prov_script.lstrip("/")
                repo_path = os.path.join(root, rel_path)
                if not os.path.exists(repo_path) and not os.path.exists(prov_script):
                    viol.append(f"[storage.cephfs].provision_script path '{prov_script}' does not exist on disk")

            if cephfs.get("automount_enable", False):
                mount_tmpl = os.path.join(root, "usr/share/mios/systemd/home-@.mount.tmpl")
                if not os.path.exists(mount_tmpl):
                    viol.append("home-@.mount.tmpl is missing from usr/share/mios/systemd/ but [storage.cephfs].automount_enable is true")

        import re
        tmpls = [
            os.path.join(root, "usr/share/mios/systemd/home-@.mount.tmpl"),
            os.path.join(root, "usr/share/mios/systemd/home-@.automount.tmpl"),
        ]
        # A token is named from its table path, [storage.cephfs].<key>. The
        # prefix was hardcoded without the "storage" segment and matched nothing
        # after the rename (0023c51d), so no token was compared and deleting
        # mount_options stayed green. A missing setup script or zero tokens is
        # now a finding, never a pass.
        pfx = "MIOS_" + "_".join(("storage", "cephfs")).upper() + "_"
        setup_code = None
        try:
            with open(os.path.join(root, "automation/firstboot/mios-cephfs-mount-setup.sh"),
                      encoding="utf-8", errors="ignore") as sf:
                setup_code = sf.read()
        except OSError:
            viol.append("automation/firstboot/mios-cephfs-mount-setup.sh is missing, so no template token is substituted")

        ntok = 0
        for tmpl in tmpls:
            if os.path.exists(tmpl):
                with open(tmpl, "r", encoding="utf-8", errors="ignore") as tf:
                    tokens = set(re.findall(r"\$\{" + pfx + r"([A-Z0-9_]+)\}", tf.read()))
                ntok += len(tokens)
                for tok in sorted(tokens):
                    key = tok.lower()
                    if key not in cephfs:
                        viol.append(f"Template token ${{{pfx}{tok}}} has no corresponding key '{key}' in [storage.cephfs]")
                    if setup_code is not None and "${%s%s}" % (pfx, tok) not in setup_code:
                        viol.append(f"Template token ${{{pfx}{tok}}} is not substituted by mios-cephfs-mount-setup.sh")
        if not ntok:
            viol.append(f"the home-@ templates carry no ${{{pfx}*}} token, so nothing was compared")

    for v in viol:
        sys.stderr.write(f"    {v}\n")
    sys.exit(1 if viol else 0)

def check_firstboot_tier() -> int:
    """Lifted from a shell heredoc so it can be imported, linted and tested.

    Inside a heredoc a syntax error surfaces only when the check runs.
    """
    import os, sys, glob
    import tomllib

    root = os.environ["MIOS_DRIFT_ROOT"]
    toml_path = os.path.join(root, "usr/share/mios/mios.toml")
    fb_list = os.path.join(root, "usr/lib/mios/bake/plan.d/firstboot.list")
    qdir = os.path.join(root, "usr/share/containers/systemd")
    bdir = os.path.join(root, "usr/lib/bootc/bound-images.d")

    if len(_scan(root, toml_path, fb_list)) < 2:
        sys.exit(0)

    with open(toml_path, "rb") as f:
        data = tomllib.load(f)

    firstboot_tokens = data.get("build", {}).get("bake", {}).get("firstboot_tokens", [])
    if not firstboot_tokens:
        sys.exit(0)

    bad = []
    fb_just = data.get("build", {}).get("bake", {}).get("firstboot_justifications", {})
    for tok in firstboot_tokens:
        if tok not in fb_just or not fb_just[tok]:
            bad.append(f"firstboot token '{tok}' has no justification in [build.bake.firstboot_justifications]")

    with open(fb_list, "r", encoding="utf-8") as fh:
        for line in fh:
            img = line.strip()
            if not img or img.startswith("#"):
                continue
            if not any(tok and tok in img for tok in firstboot_tokens):
                bad.append(f"firstboot.list entry '{img}' matches no token in firstboot_tokens")

    if os.path.isdir(bdir):
        for q in sorted(glob.glob(os.path.join(qdir, "*.container")) + glob.glob(os.path.join(qdir, "*.image"))):
            name = os.path.basename(q)
            img = ""
            try:
                with open(q, "r", encoding="utf-8", errors="ignore") as fh:
                    for line in fh:
                        if line.strip().startswith("Image="):
                            img = line.strip()[6:].strip()
                            break
            except OSError:
                pass
            if any(tok and tok in img for tok in firstboot_tokens):
                if os.path.lexists(os.path.join(bdir, name)):
                    bad.append(f"Firstboot-tier Quadlet '{name}' ({img}) is wrongly symlinked under bound-images.d")

    consumer_script = os.path.join(root, "usr/libexec/mios/mios-ai-firstboot")
    if os.path.isfile(consumer_script):
        with open(consumer_script, "r", encoding="utf-8", errors="ignore") as fh:
            if "firstboot.list" not in fh.read():
                bad.append("usr/libexec/mios/mios-ai-firstboot does not reference firstboot.list")

    if bad:
        for b in bad:
            sys.stderr.write(f"    {b}\n")
        sys.exit(1)
    sys.exit(0)

def check_bound_image_store() -> int:
    """Keep bootc's read-only image store scoped to bound system Quadlets."""
    import glob
    import os
    import shlex
    import sys
    import tomllib

    root = os.path.abspath(os.environ["MIOS_DRIFT_ROOT"])
    toml_path = os.path.join(root, "usr/share/mios/mios.toml")
    qdirs = [os.path.join(root, "usr/share/containers/systemd"),
             os.path.join(root, "etc/containers/systemd")]
    bdir = os.path.join(root, "usr/lib/bootc/bound-images.d")
    if not os.path.isfile(toml_path) or not os.path.isdir(qdirs[0]):
        print("bound-image-store: SSOT or generated Quadlet directory is missing", file=sys.stderr)
        return 1
    with open(toml_path, "rb") as fh:
        bake = (tomllib.load(fh).get("build") or {}).get("bake") or {}
    store = bake.get("additional_image_store")
    tokens = bake.get("firstboot_tokens", [])
    if not isinstance(store, str) or not store.startswith("/") or any(c.isspace() for c in store):
        print("bound-image-store: additional_image_store must be an absolute path without whitespace", file=sys.stderr)
        return 1
    if not isinstance(tokens, list) or any(not isinstance(t, str) for t in tokens):
        print("bound-image-store: firstboot_tokens must be a string array", file=sys.stderr)
        return 1

    bad = []
    definitions = {}
    # Match overlay-bind-images precedence: vendor first, host overrides last.
    for qdir in qdirs:
        paths = []
        for extension in ("container", "image"):
            paths.extend(glob.glob(os.path.join(qdir, "*." + extension)))
            paths.extend(glob.glob(os.path.join(qdir, "*", "*." + extension)))
        for path in sorted(paths):
            section, images, args = "", [], []
            main_section = "Container" if path.endswith(".container") else "Image"
            with open(path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if line.startswith("[") and line.endswith("]"):
                        section = line[1:-1]
                    elif section == main_section and "=" in line and not line.startswith(("#", ";")):
                        key, value = (part.strip() for part in line.split("=", 1))
                        if key == "Image":
                            images.append(value)
                        elif key == "GlobalArgs":
                            args.append(value)
            definitions[os.path.basename(path)] = (path, images, args)
    if not definitions:
        bad.append("no system or user Quadlet image definitions found")

    expected_bound = {}
    firstboot = set()
    for name, (path, images, args) in definitions.items():
        if len(images) != 1 or not images[0]:
            bad.append(f"{name}: expected one nonempty Image= value")
            continue
        try:
            words = [word for arg in args for word in shlex.split(arg)]
        except ValueError as exc:
            bad.append(f"{name}: invalid GlobalArgs quoting: {exc}")
            continue
        stores = []
        for index, word in enumerate(words):
            if word.startswith("--storage-opt=additionalimagestore="):
                stores.append(word.removeprefix("--storage-opt=additionalimagestore="))
            elif word == "--storage-opt" and index + 1 < len(words):
                option = words[index + 1]
                if option.startswith("additionalimagestore="):
                    stores.append(option.removeprefix("additionalimagestore="))
        is_firstboot = any(token and token in images[0] for token in tokens)
        is_user = os.path.basename(os.path.dirname(path)) == "users"
        if is_firstboot:
            firstboot.add(name)
        else:
            expected_bound[name] = path
        if is_firstboot or is_user:
            if stores:
                bad.append(f"{name}: firstboot or user-scope image must not use bootc's image store")
        elif name.endswith(".container") and stores != [store]:
            bad.append(f"{name}: expected one additionalimagestore={store} argument")

    # .gitkeep is the explicit source-only placeholder, removed by the bake.
    actual = {name for name in os.listdir(bdir) if name != ".gitkeep"} if os.path.isdir(bdir) else set()
    source_only = not actual and os.path.isfile(os.path.join(bdir, ".gitkeep"))
    if not source_only:
        for name in actual:
            link = os.path.join(bdir, name)
            if not os.path.islink(link) or not os.path.exists(link):
                bad.append(f"bound-images.d/{name}: missing or broken symlink")
            if name in firstboot or name not in expected_bound:
                bad.append(f"bound-images.d/{name}: not a declared bound Quadlet")
            elif os.path.islink(link) and os.path.normcase(os.path.realpath(link)) != os.path.normcase(os.path.realpath(expected_bound[name])):
                bad.append(f"bound-images.d/{name}: symlink targets wrong Quadlet")
        for name in expected_bound.keys() - actual:
            bad.append(f"bound-images.d/{name}: missing bound Quadlet symlink")

    def globally_enabled(value):
        if isinstance(value, dict):
            return any((key == "additionalimagestores" and isinstance(item, list) and store in item)
                       or globally_enabled(item) for key, item in value.items())
        return False

    for relative in ("etc/containers", "usr/share/containers"):
        config_dir = os.path.join(root, relative)
        configs = [os.path.join(config_dir, "storage.conf")]
        configs.extend(glob.glob(os.path.join(config_dir, "storage.conf.d", "*.conf")))
        for path in configs:
            if not os.path.isfile(path):
                continue
            rel = os.path.relpath(path, root).replace("\\", "/")
            try:
                with open(path, "rb") as fh:
                    enabled = globally_enabled(tomllib.load(fh))
            except (OSError, tomllib.TOMLDecodeError) as exc:
                bad.append(f"{rel}: cannot inspect storage config: {exc}")
                continue
            if enabled:
                bad.append(f"{rel}: bootc store must not be enabled globally")
    for item in sorted(bad):
        print(f"bound-image-store: {item}", file=sys.stderr)
    return 1 if bad else 0

def check_gate_registry() -> int:
    """Compatibility entrypoint; native Rust owns bounded registration checks."""
    import os
    import subprocess
    from pathlib import Path

    wrapper = Path(__file__).resolve().parents[1] / "automation/98-drift-checks.sh"
    env = os.environ.copy()
    env["MIOS_DRIFT_CHECK_ROOT"] = env.get("MIOS_DRIFT_ROOT", ".")
    return subprocess.call(["bash", str(wrapper), "check_gate_registry"], env=env)

def check_agent_schema() -> int:
    """Lifted from a shell heredoc so it can be imported, linted and tested.

    Inside a heredoc a syntax error surfaces only when the check runs.
    """
    import os, sys, re
    root = os.environ["MIOS_DRIFT_ROOT"]
    import tomllib as _toml
    p = os.path.join(root, "usr/share/mios/mios.toml")
    _rc = _absent(root, p)
    if _rc is not None:
        sys.exit(_rc)
    with open(p, "rb") as fh:
        d = _toml.load(fh)
    ag = dict(d.get("agents") or {})
    defs = ag.pop("_defaults", {}) if isinstance(ag.get("_defaults"), dict) else {}
    CANON = {"kind","endpoint","model","role","job","default","fanout","enabled","lane",
             "sub_lane","health_gate","transport","timeout_s","strengths","cpu_endpoint",
             "cpu_model","failover_agents","denied_verbs","allowed_verbs","max_permission",
             "api","vram_mb","ram_mb","tool_capable","research_only","auth","trust",
             "engines","nodes","backend","privilege_group"}
    def _local(ep):
        h = re.sub(r'^[a-z]+://', '', str(ep)).split('/')[0].rsplit(':', 1)[0]
        return h in ("localhost", "127.0.0.1", "::1", "0.0.0.0", "")
    bad, warn, ndefault = [], [], 0
    REQUIRED_FIELDS = {"role", "job", "lane", "health_gate"}
    for name, cfg in ag.items():
        if name.startswith("_") or not isinstance(cfg, dict):
            continue
        if not cfg:
            bad.append(f"    [agents.{name}] agent table is empty")
            continue
        for req_k in REQUIRED_FIELDS:
            if req_k not in cfg:
                bad.append(f"    [agents.{name}] missing required field {req_k!r} in block")
        if "model" not in cfg and "endpoint" not in cfg:
            bad.append(f"    [agents.{name}] must declare 'model' or 'endpoint' in block")
        m = {**defs, **cfg}
        kind = str(m.get("kind", "")).strip().lower()
        ep = str(m.get("endpoint", "")).strip()
        enabled = bool(m.get("enabled", True))
        hg = bool(m.get("health_gate", False))
        if bool(m.get("default", False)):
            ndefault += 1
        loc = _local(ep)
        if loc and not bool(m.get("default", False)) and enabled and kind in ("", "local-http") and not hg:
            bad.append(f"    [agents.{name}] LOCAL + non-default + enabled but no health_gate=true (or enabled=false): a dead endpoint is treated as live -> DAG sink -> merged_chars=0")
        if kind == "cli":
            if not (hg or not enabled):
                bad.append(f"    [agents.{name}] kind=cli must set health_gate=true OR enabled=false")
            if int(m.get("timeout_s", 0) or 0) <= 0:
                bad.append(f"    [agents.{name}] kind=cli must set timeout_s>0 (fail-fast budget)")
        if kind == "node" and not (str(m.get("api", "")).strip() and str(m.get("lane", "")).strip()):
            bad.append(f"    [agents.{name}] kind=node must set api + lane")
        if kind in ("remote-http", "edge", "mobile") and not hg:
            bad.append(f"    [agents.{name}] kind={kind} must set health_gate=true")
        if re.search(r':\d{2,5}(/|$)', ep) and "${MIOS_PORT" not in ep:
            warn.append(f"    [agents.{name}].endpoint bare :PORT literal (use ${{MIOS_PORT_*}}): {ep}")
        for k in cfg:
            if k not in CANON:
                warn.append(f"    [agents.{name}] unknown key {k!r} (not in the canonical agent schema)")
    if ndefault > 1:
        bad.append(f"    {ndefault} [agents.*] set default=true; at most one is allowed")
    for w in warn:
        sys.stdout.write("[98-drift-checks]   (advisory)" + w + "\n")
    for b in bad:
        sys.stderr.write(b + "\n")
    sys.exit(1 if bad else 0)

def check_rbac_tiers() -> int:
    import os, sys
    import tomllib as _toml
    root = os.environ.get("MIOS_DRIFT_ROOT", ".")
    p = os.path.join(root, "usr/share/mios/mios.toml")
    _rc = _absent(root, p)
    if _rc is not None:
        return _rc
    with open(p, "rb") as fh:
        d = _toml.load(fh)
    tiers = [str(x).strip().lower()
             for x in ((d.get("ai") or {}).get("permission_tiers")
                       or ["read", "write", "interactive"]) if str(x).strip()]
    bad = []
    for sect in ("agents", "users"):
        for name, cfg in (d.get(sect) or {}).items():
            if not isinstance(cfg, dict):
                continue
            mp = str(cfg.get("max_permission") or "").strip().lower()
            if mp and mp not in tiers:
                bad.append(f"    [{sect}.{name}].max_permission={mp!r} not in {tiers}")
    for b in bad:
        sys.stderr.write(b + "\n")
    return 1 if bad else 0

def check_ai_manifest() -> int:
    import os, sys, json
    root = os.environ.get("MIOS_DRIFT_ROOT", ".")
    sys.path.insert(0, os.path.join(root, "usr/lib/mios/agent-pipe"))
    # The module is a tracked deliverable, so "cannot import" was a dropped
    # subject reported as a pass. It imports stdlib only -- no dep can be absent.
    _rc = _absent(root, os.path.join(root, "usr/lib/mios/agent-pipe/mios_manifest.py"))
    if _rc is not None:
        return _rc
    try:
        import mios_manifest as man
    except Exception as e:
        sys.stderr.write("    mios_manifest is present but did not import (%s), so "
                         "the verb catalogue was never projected\n" % e)
        return 1
    toml = os.path.join(root, "usr/share/mios/mios.toml")
    out = os.path.join(root, "usr/share/mios/ai/v1/tools.generated.json")
    try:
        gen = man.project_verb_catalog(man.load_verbs_from_toml(toml))
    except Exception as e:
        sys.stderr.write(f"    verb-catalog projection failed: {e}\n")
        return 1
    try:
        with open(out, encoding="utf-8") as fh:
            committed = json.load(fh)
    except (OSError, ValueError) as e:
        sys.stderr.write(f"    committed manifest unreadable ({out}): {e}\n")
        return 1
    diffs = man.diff_manifest(gen, committed)
    for d in diffs[:30]:
        sys.stderr.write("    " + d + "\n")
    return 1 if diffs else 0

def check_capability_manifest() -> int:
    import os, sys, json
    root = os.environ.get("MIOS_DRIFT_ROOT", ".")
    sys.path.insert(0, os.path.join(root, "usr/lib/mios/agent-pipe"))
    _rc = _absent(root, os.path.join(root, "usr/lib/mios/agent-pipe/mios_capreg.py"))
    if _rc is not None:
        return _rc
    try:
        import mios_capreg as cap
    except Exception as e:
        sys.stderr.write("    mios_capreg is present but did not import (%s), so the "
                         "capability registry was never projected\n" % e)
        return 1
    toml = os.path.join(root, "usr/share/mios/mios.toml")
    out = os.path.join(root, "usr/share/mios/ai/v1/capabilities.generated.json")
    try:
        gen = cap.project_from_toml(toml, ceiling="interactive")
    except Exception as e:
        sys.stderr.write(f"    capability projection failed: {e}\n")
        return 1
    try:
        with open(out, encoding="utf-8") as fh:
            committed = json.load(fh).get("data", [])
    except (OSError, ValueError) as e:
        sys.stderr.write(f"    committed capabilities manifest unreadable ({out}): {e}\n")
        return 1
    diffs = cap.diff_capabilities(gen, committed)
    for d in diffs[:30]:
        sys.stderr.write("    " + d + "\n")
    return 1 if diffs else 0

def check_surface_parity() -> int:
    import os, sys, json
    root = os.environ.get("MIOS_DRIFT_ROOT", ".")
    sys.path.insert(0, os.path.join(root, "usr/lib/mios/agent-pipe"))
    _rc = _absent(root, os.path.join(root, "usr/lib/mios/agent-pipe/mios_surface.py"))
    if _rc is not None:
        return _rc
    try:
        import mios_surface as surf
    except Exception as e:
        sys.stderr.write("    mios_surface is present but did not import (%s), so the "
                         "served surface was never projected\n" % e)
        return 1
    server = os.path.join(root, "usr/lib/mios/agent-pipe/server.py")
    out = os.path.join(root, "usr/share/mios/ai/v1/surface.generated.json")
    # server.py is tracked too: absent, it is the subject going missing.
    _rc = _absent(root, server)
    if _rc is not None:
        return _rc
    try:
        gen = surf.project_package(server)
    except Exception as e:
        sys.stderr.write(f"    surface projection failed: {e}\n")
        return 1
    try:
        with open(out, encoding="utf-8") as fh:
            committed = json.load(fh)
    except (OSError, ValueError) as e:
        sys.stderr.write(f"    committed surface golden unreadable ({out}): {e}\n")
        return 1
    diffs = surf.diff_surface(gen, committed)
    for d in diffs[:40]:
        sys.stderr.write("    " + d + "\n")
    if len(diffs) > 40:
        sys.stderr.write(f"    ... and {len(diffs) - 40} more\n")
    return 1 if diffs else 0

def check_container_ports() -> int:
    import os, sys, re
    root = os.environ.get("MIOS_DRIFT_ROOT", ".")
    import tomllib as _toml

    p = os.path.join(root, "usr/share/mios/mios.toml")
    _rc = _absent(root, p)
    if _rc is not None:
        return _rc

    with open(p, "rb") as fh:
        d = _toml.load(fh)
    ports = d.get("ports") or {}

    # Twin of container_ports in src/mios-rs/miosd/src/drift/ports.rs (Law 13).
    # bool is an int subclass in Python; the native side reads integers only.
    port_vals = {name: val for name, val in ports.items()
                 if name != "stack_id" and isinstance(val, int) and not isinstance(val, bool)}
    if not port_vals:
        print("SSOT [ports] declares no integer port, so no Quadlet literal can be recognised",
              file=sys.stderr)
        return 1
    # Container-side listening ports. This used to hard-code (8080, 3002), the
    # SearXNG and firecrawl upstream internals; SSOT now names that class
    # `*_internal`. They may appear as the container side of a mapping or in an
    # in-container `X=N`, never as a bare host-side `PublishPort=N`.
    internal = {val for name, val in port_vals.items() if name.endswith("_internal")}
    # MIOS_PORTS_<KEY> is the canonical [ports] env name. The fallback regex knew
    # only the older MIOS_PORT_ prefix, so every SSOT-wired `${MIOS_PORTS_X:-N}`
    # in the tree was reported as a hand-copied literal.
    patterns = [(name, val,
                 re.compile(r'\$\{MIOS_' + r'PORTS?_[A-Z0-9_]+:-' + str(val) + r'\}'),
                 re.compile(rf'\b{val}\b'))
                for name, val in port_vals.items()]

    viol = []
    subjects = 0
    quadlet_dirs = ["usr/share/containers/systemd", "etc/containers/systemd"]
    for qd in quadlet_dirs:
        dir_path = os.path.join(root, qd)
        if not os.path.isdir(dir_path):
            continue
        for dp, _dn, files in os.walk(dir_path):
            for fn in sorted(files):
                if not fn.endswith(".container"):
                    continue
                path = os.path.join(dp, fn)
                try:
                    lines = open(path, encoding="utf-8", errors="ignore").read().splitlines()
                except OSError as exc:
                    viol.append(f"{fn}: unreadable, so its ports were never compared: {exc}")
                    continue
                subjects += 1
                for idx, line in enumerate(lines, 1):
                    # systemd unit syntax: only a WHOLE line starting with # or ;
                    # is a comment. Stripping `#.*` also cut active values at a
                    # mid-line # (URL fragments, `$#`), hiding literals that run.
                    active = line.strip()
                    if not active or active.startswith(("#", ";")):
                        continue
                    for name, val, fallback, literal in patterns:
                        cleaned = fallback.sub("", active)
                        if not literal.search(cleaned):
                            continue
                        container_side = (f":{val}" in cleaned
                                          or (f"={val}" in cleaned
                                              and not cleaned.startswith("PublishPort=")))
                        if val in internal and container_side:
                            continue
                        viol.append(f"{fn}:{idx}: manual port literal {val} for [ports].{name}; "
                                    f"write ${{MIOS_PORTS_{name.upper()}:-{val}}} so the SSOT "
                                    f"value reaches the unit: {active}")

    if subjects == 0:
        print("no Quadlet .container file was read under " + ", ".join(quadlet_dirs)
              + " -- the corpus is wrong, so an empty result is not a pass", file=sys.stderr)
        return 1
    for v in viol:
        print(v)
    return 1 if viol else 0

def check_agent_pipe_budgets() -> int:
    """Compatibility entrypoint for the complete native SSOT budget census."""
    import os
    import subprocess
    from pathlib import Path

    wrapper = Path(__file__).resolve().parents[1] / "automation/98-drift-checks.sh"
    env = os.environ.copy()
    env["MIOS_DRIFT_CHECK_ROOT"] = env.get("MIOS_DRIFT_ROOT", ".")
    return subprocess.run(["bash", str(wrapper), "check_agent_pipe_budgets"], env=env).returncode

def check_verb_backends() -> int:
    import os, sys, re
    root = os.environ.get("MIOS_DRIFT_ROOT", ".")
    import tomllib as _toml
    p = os.path.join(root, "usr/share/mios/mios.toml")
    _rc = _absent(root, p)
    if _rc is not None:
        return _rc
    with open(p, "rb") as fh:
        d = _toml.load(fh)
    libexec = os.path.join(root, "usr/libexec/mios")
    usrbin = os.path.join(root, "usr/bin")
    def _exists(t):
        return os.path.isfile(os.path.join(libexec, t)) or os.path.isfile(os.path.join(usrbin, t))
    missing = {}
    for name, cfg in (d.get("verbs", {}) or {}).items():
        if not isinstance(cfg, dict):
            continue
        cmd = cfg.get("cmd", "")
        if name == "update" and not cmd:
            missing.setdefault("update missing cmd key", []).append(name)
            continue
        if not isinstance(cmd, str) or not cmd:
            continue
        for tok in set(re.findall(r"\bmios-[a-z0-9-]+", cmd)):
            if not _exists(tok):
                missing.setdefault(tok, []).append(name)
    for t, vs in sorted(missing.items()):
        sys.stderr.write(f"    {t} <- [verbs.*] {sorted(vs)} (backend not on disk)\n")
    return 1 if missing else 0

def check_dag_integrity() -> int:
    import os, sys, re
    root = os.environ.get("MIOS_DRIFT_ROOT", ".")
    violations = []

    scan_dirs = [
        os.path.join(root, "usr/lib/systemd/system"),
        os.path.join(root, "usr/share/containers/systemd"),
    ]

    for d in _scan(root, *scan_dirs):
        for f in os.listdir(d):
            fpath = os.path.join(d, f)
            if not os.path.isfile(fpath) or not f.endswith((".service", ".container", ".pod")):
                continue
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as fh:
                    content = fh.read()

                after_requires_targets = []
                for line in content.splitlines():
                    m = re.match(r"^[ \t]*(After|Requires)[ \t]*=[ \t]*(.*)$", line, re.IGNORECASE)
                    if m:
                        after_requires_targets.extend(m.group(2).split())

                is_local_img = "Image=localhost/" in content
                is_webtools_pod = f == "mios-webtools.pod"
                if is_local_img or is_webtools_pod:
                    if "mios-webtools-firstboot.service" not in after_requires_targets:
                        violations.append(f"{f} uses local image/pod but lacks 'After=... mios-webtools-firstboot.service'")
            except OSError:
                pass

    if violations:
        for v in sorted(violations):
            sys.stderr.write(f"    {v}\n")
        return 1
    return 0

def check_ai_endpoint_local() -> int:
    import os, re, sys
    import tomllib as _t
    root = os.environ.get("MIOS_DRIFT_ROOT", ".")
    with open(os.path.join(root, "usr/share/mios/mios.toml"), "rb") as fh:
        data = _t.load(fh)
    ep = str((data.get("ai") or {}).get("endpoint") or "")
    if not ep:
        print("[ai].endpoint is empty -- every client resolves MIOS_AI_ENDPOINT from it")
        return 0
    host = re.sub(r"^[a-z]+://", "", ep).split("/")[0].split(":")[0]
    if host not in ("localhost", "127.0.0.1", "::1", "[::1]"):
        print("[ai].endpoint is %s: the VENDOR default must stay local (ADR-0016 D5). "
              "Point it off-box in /etc/mios, never in the shipped SSOT" % ep)
        return 1
    return 0

def check_bake_refs_parity() -> int:
    import os, sys, re, subprocess
    import tomllib

    root = os.environ.get("MIOS_DRIFT_ROOT", ".")
    toml_path = os.path.join(root, "usr/share/mios/mios.toml")
    if not os.path.isfile(toml_path):
        # A tracked deliverable. Its absence is the anomaly, not a
        # reason to report success.
        print('check_bake_refs_parity: a required SSOT file is missing, so nothing was'
              ' compared', file=sys.stderr)
        return 1

    with open(toml_path, "rb") as f:
        data = tomllib.load(f)

    bake_refs = data.get("build", {}).get("bake_refs", {})

    # git grep exits 1 for "no match", legitimate here (3 of 6 bake_refs carry a
    # shell default); `except Exception` also swallowed 128, which is not.
    try:
        p = subprocess.run(["git", "-C", root, "grep", "-E",
                            r"MIOS_BUILD_BAKE_REFS_[A-Z0-9_]+:-", "--", "automation/"],
                           capture_output=True, text=True)
    except OSError as exc:
        print("check_bake_refs_parity: git could not be run in %s (%s), so no "
              "baker default was compared" % (root, exc), file=sys.stderr)
        return 1
    if p.returncode > 1:
        print("check_bake_refs_parity: git grep exit %d (%s), so no baker "
              "default was compared"
              % (p.returncode, (p.stderr or "").strip() or "no message"),
              file=sys.stderr)
        return 1
    matches = p.stdout.splitlines()

    viol = []
    pattern = re.compile(r"MIOS_BUILD_BAKE_REFS_([A-Z0-9_]+):-([^}\"\']+)")
    for m in matches:
        res = pattern.search(m)
        if res:
            key = res.group(1).lower()
            lit = res.group(2).strip()
            if key in bake_refs:
                ssot_val = str(bake_refs[key]).strip()
                if lit != ssot_val:
                    viol.append(f"{m.split(':')[0]}: MIOS_BUILD_BAKE_REFS_{res.group(1)} default '{lit}' != SSOT '{ssot_val}'")

    if viol:
        for v in viol:
            sys.stderr.write(f"    {v}\n")
        return 1
    return 0

def check_cli_eval_safety() -> int:
    import os, sys, re
    root = os.environ.get("MIOS_DRIFT_ROOT", ".")
    dir_to_scan = os.path.join(root, "usr/libexec/mios")
    viol = []

    # os.listdir reached only the top level, so a verb backend one directory
    # down was never read for eval at all.
    if not os.path.isdir(dir_to_scan):
        print("usr/libexec/mios is absent, so no verb backend was read for eval",
              file=sys.stderr)
        return 1

    # A reviewed eval of non-agent input carries this annotation on the line
    # DIRECTLY above it -- the remedy the message below prescribes. 5f2aadbf
    # dropped the exemption and kept the message, so following the remedy no
    # longer cleared the violation. Twin of eval_safety in
    # src/mios-rs/miosd/src/drift/security.rs (Law 13).
    attested = re.compile(r"^#\s*TD-1:\s*eval-safe,\s*input=.+,\s*not agent-controlled")

    scanned = 0
    for dirpath, dirnames, filenames in os.walk(dir_to_scan):
        dirnames[:] = [d for d in dirnames
                       if not d.startswith(".") and d not in ("__pycache__", "node_modules")]
        for fn in filenames:
            path = os.path.join(dirpath, fn)
            if not os.path.isfile(path) or fn.endswith((".py", ".pyc", ".json", ".generated")):
                continue
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                    first_line = fh.readline()
                    if not ("bash" in first_line or "sh" in first_line):
                        continue
                    fh.seek(0)
                    lines = fh.readlines()
            except OSError:
                continue

            scanned += 1
            rel = os.path.relpath(path, dir_to_scan).replace(os.sep, "/")
            for idx, line in enumerate(lines):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue

                code_part = line.split("#")[0].strip()
                if re.search(r'\beval\b', code_part):
                    if idx > 0 and attested.match(lines[idx - 1].strip()):
                        continue
                    viol.append(f"{rel}:{idx+1} has eval: {line.strip()}")

    if scanned < 20:
        print("only %d shell verb backend(s) read under usr/libexec/mios -- the "
              "corpus is wrong, so an empty result is not a pass" % scanned,
              file=sys.stderr)
        return 1

    if viol:
        for v in viol:
            sys.stderr.write(f"  {v}\n")
        sys.stderr.write("  verbs must not eval agent-controlled inputs; a pre-existing "
                         "safe eval needs a preceding "
                         "# TD-1: eval-safe, input=<source>, not agent-controlled comment\n")
        return 1

    # TD-2: ban os.system() across all scripts in usr/libexec/mios
    os_sys_viol = []
    for dirpath, dirnames, filenames in os.walk(dir_to_scan):
        dirnames[:] = [d for d in dirnames
                       if not d.startswith(".") and d not in ("__pycache__", "node_modules")]
        for fn in filenames:
            path = os.path.join(dirpath, fn)
            if not os.path.isfile(path) or fn.startswith("test_") or fn.endswith((".pyc", ".json", ".generated", ".png", ".jpg")):
                continue
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                    for idx, line in enumerate(fh, 1):
                        stripped = line.strip()
                        if stripped.startswith("#"):
                            continue
                        code_part = line.split("#")[0].strip()
                        if re.search(r'\bos\.system\s*\(', code_part):
                            rel = os.path.relpath(path, dir_to_scan).replace(os.sep, "/")
                            os_sys_viol.append(f"{rel}:{idx} has os.system: {line.strip()}")
            except OSError:
                continue

    if os_sys_viol:
        for v in os_sys_viol:
            sys.stderr.write(f"  {v}\n")
        sys.stderr.write("  os.system() is forbidden under usr/libexec/mios (TD-2); "
                         "use subprocess.run([...], check=...) with an argv list instead\n")
        return 1

    print("%d shell verb backend(s) read for eval; os.system banned under usr/libexec/mios" % scanned)
    return 0

def check_resolver_ssot_refs() -> int:
    import os, sys, re
    root = os.environ.get("MIOS_DRIFT_ROOT", ".")
    rel = os.environ.get("MIOS_DRIFT_REL", "usr/libexec/mios/mios-resolve-latest")
    path = os.path.join(root, rel)
    _rc = _absent(root, path)
    if _rc is not None:
        return _rc
    ref = re.compile(r"""['"][a-z0-9][a-z0-9.\-]*\.[a-z]{2,}/[^\s'"]+:[^\s'"]+['"]""")
    res = []
    with open(path, encoding="utf-8", errors="ignore") as fh:
        for i, line in enumerate(fh, 1):
            s = line.strip()
            if s.startswith("#"):
                continue
            m = ref.search(s)
            if m:
                res.append(f"{i}: {m.group(0)}")
    if res:
        for r in res:
            print(r)
        return 1
    return 0

def check_bake_budget() -> int:
    import os, sys, tomllib

    root = os.environ.get("MIOS_DRIFT_ROOT", ".")
    toml_path = os.path.join(root, "usr/share/mios/mios.toml")
    tsv_path = os.path.join(root, "usr/share/mios/artifacts/sbom/bound-images.tsv")

    if not os.path.exists(toml_path):
        print("ERROR: SSOT mios.toml absent")
        return 1

    if not os.path.exists(tsv_path):
        print("ERROR: bound-images.tsv SBOM artifact absent")
        return 1

    try:
        with open(toml_path, "rb") as f:
            data = tomllib.load(f)
    except Exception as e:
        print(f"ERROR: Failed to parse mios.toml: {e}")
        return 1

    budget = data.get("build", {}).get("bake", {}).get("runner_disk_budget_gb", None)
    if budget is None or not isinstance(budget, (int, float)) or budget <= 0:
        print(f"ERROR: [build.bake].runner_disk_budget_gb is absent or invalid ({budget})")
        return 1

    try:
        with open(tsv_path, "r", encoding="utf-8") as f:
            lines = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    except Exception as e:
        print(f"ERROR: Failed to read bound-images.tsv: {e}")
        return 1

    if not lines:
        print("ERROR: bound-images.tsv is empty")
        return 1

    header = lines[0].split("\t")
    if "size_gb" not in header:
        print("ERROR: bound-images.tsv missing size_gb column")
        return 1

    size_idx = header.index("size_gb")
    group_idx = header.index("group") if "group" in header else -1

    total_day0 = 0.0
    for line in lines[1:]:
        parts = line.split("\t")
        group = parts[group_idx] if group_idx >= 0 and len(parts) > group_idx else "extra"
        if group == "firstboot":
            continue
        try:
            sz = float(parts[size_idx])
        except (ValueError, IndexError):
            print(f"ERROR: Malformed size entry in line: {line}")
            return 1
        total_day0 += sz

    if total_day0 > budget:
        print(f"EXCEEDED: Total Day-0 bake size {total_day0:.2f}GB exceeds SSOT budget {budget}GB")
        return 1

    print(f"OK: Day-0 size {total_day0:.2f}GB <= budget {budget}GB")
    return 0

def check_greenboot() -> int:
    import os, re, sys
    import tomllib as _toml

    root = os.environ.get("MIOS_DRIFT_ROOT", ".")
    gb_dir = os.environ.get("MIOS_DRIFT_GB_DIR", os.path.join(root, "usr/lib/greenboot/check/required.d"))
    toml_path = os.path.join(root, "usr/share/mios/mios.toml")
    if not os.path.isfile(toml_path):
        # A tracked deliverable. Its absence is the anomaly, not a
        # reason to report success.
        print('check_greenboot: a required SSOT file is missing, so nothing was'
              ' compared', file=sys.stderr)
        return 1
    with open(toml_path, "rb") as fh:
        data = _toml.load(fh)
    gb = data.get("greenboot") or {}
    critical = [str(x).strip() for x in (gb.get("critical_services") or []) if str(x).strip()]
    probe = gb.get("probe") or {}
    if not critical:
        print("(54) [greenboot].critical_services is empty or absent -- greenboot coverage would pass vacuously over an empty set")
        return 1
    if "hermes" not in critical:
        print("(54) [greenboot].critical_services must list 'hermes' as a critical service")
        return 1

    bodies, probed, ssot_driven = {}, set(), False
    if os.path.isdir(gb_dir):
        for name in sorted(os.listdir(gb_dir)):
            if not name.endswith(".sh"):
                continue
            fp = os.path.join(gb_dir, name)
            if not os.path.isfile(fp):
                continue
            try:
                body = open(fp, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            code = "\n".join(l for l in body.splitlines() if not l.lstrip().startswith("#"))
            bodies[name] = code
            if "MIOS_GREENBOOT_CRITICAL_SERVICES" in code:
                ssot_driven = True
            for m in re.finditer(r"\b(?:mios-)?([a-z0-9][a-z0-9_-]*)\.service\b", code):
                probed.add(m.group(1))

    def unit_for(svc):
        spec = probe.get(svc.replace("-", "_")) or probe.get(svc) or {}
        unit = str(spec.get("unit") or "").strip()
        return unit or ("mios-%s.service" % svc)

    def unit_exists(unit):
        stem = unit[:-len(".service")] if unit.endswith(".service") else unit
        if stem in (data.get("containers") or {}):
            return True
        return os.path.isfile(os.path.join(root, "usr/lib/systemd/system", unit))

    viol = []
    for svc in critical:
        if ssot_driven:
            unit = unit_for(svc)
            if not unit_exists(unit):
                viol.append("(54) [greenboot].critical_services names '%s', but the probe would derive %s, which is not a shipped unit or a declared container" % (svc, unit))
            continue
        key = svc[5:] if svc.startswith("mios-") else svc
        if key not in probed:
            viol.append("(54) greenboot missing health-check script for critical service: %s (no required.d script references %s.service outside comments)" % (svc, svc))

    if viol:
        for v in viol:
            print(v)
        return 1
    return 0

def check_router_intent_coverage() -> int:
    import sys, json, re, glob, os

    if len(sys.argv) >= 4:
        corpus_file, root_dir = sys.argv[2], sys.argv[3]
    elif len(sys.argv) == 3:
        corpus_file, root_dir = sys.argv[2], os.environ.get("MIOS_DRIFT_ROOT", ".")
    else:
        root_dir = os.environ.get("MIOS_DRIFT_ROOT", ".")
        corpus_file = os.path.join(root_dir, "usr/lib/mios/agent-pipe/tests/router_corpus.json")

    _rc = _absent(root_dir, corpus_file)
    if _rc is not None:
        return _rc

    with open(corpus_file, "r", encoding="utf-8") as f:
        corpus = json.load(f)

    corpus_intents = set()
    for item in corpus:
        inp = item.get("input", {})
        if isinstance(inp, dict) and "intent" in inp and inp["intent"]:
            corpus_intents.add(str(inp["intent"]).strip().lower())

    pattern = re.compile(r'(?:intent\s*==|get\s*\(\s*["\']intent["\']\s*\)\s*==)\s*["\']([a-zA-Z0-9_]+)["\']')

    search_files = [os.path.join(root_dir, "usr/lib/mios/agent-pipe/server.py")] + \
                   glob.glob(os.path.join(root_dir, "usr/lib/mios/agent-pipe/mios_pipe/**/*.py"), recursive=True)

    unmapped = set()
    for filepath in search_files:
        if not os.path.isfile(filepath) or "test_" in os.path.basename(filepath):
            continue
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
            for match in pattern.finditer(content):
                intent_val = match.group(1).lower()
                if intent_val not in corpus_intents:
                    unmapped.add((intent_val, os.path.relpath(filepath, root_dir).replace("\\", "/")))
        except Exception:
            pass

    if unmapped:
        for intent_val, relpath in sorted(unmapped):
            print(f"unmapped intent: {intent_val} in {relpath}", file=sys.stderr)
        return 1
    return 0

def check_council_gate_ssot() -> int:
    import os, sys, re
    import tomllib

    root = os.environ.get("MIOS_DRIFT_ROOT", ".")
    toml_path = os.path.join(root, "usr/share/mios/mios.toml")
    if not os.path.isfile(toml_path):
        # A tracked deliverable. Its absence is the anomaly, not a
        # reason to report success.
        print('check_council_gate_ssot: a required SSOT file is missing, so nothing was'
              ' compared', file=sys.stderr)
        return 1

    with open(toml_path, "rb") as f:
        data = tomllib.load(f)

    agent_pipe = data.get("agent_pipe", {})
    council = agent_pipe.get("council", {})
    if not council:
        sys.stderr.write("    Missing [agent_pipe.council] table in mios.toml\n")
        return 1

    search_dir = os.path.join(root, "usr/lib/mios/agent-pipe")
    if not os.path.isdir(search_dir):
        search_dir = root

    code = ""
    for r, ds, fs in os.walk(search_dir):
        for f in fs:
            if f.endswith(".py"):
                try:
                    with open(os.path.join(r, f), "r", encoding="utf-8", errors="ignore") as fh:
                        code += fh.read() + "\n"
                except OSError:
                    pass

    council_keys = ["diversity_gate", "diversity_threshold", "aggregator_bypass", "aggregator_bypass_threshold"]
    missing = []
    for k in council_keys:
        if k not in council:
            missing.append(f"{k} (missing from mios.toml)")
            continue
        pattern = rf"['\"]{k}['\"]"
        if not re.search(pattern, code) and k not in code:
            missing.append(k)

    if missing:
        sys.stderr.write(f"    Missing code consumers or TOML definitions for [agent_pipe.council] keys: {missing}\n")
        return 1
    return 0

def check_test_hermeticity() -> int:
    import os, sys, glob, re

    root = os.environ.get("MIOS_DRIFT_ROOT", ".")
    search_dirs = [
        os.path.join(root, "usr/lib/mios/agent-pipe"),
        os.path.join(root, "tests"),
    ]

    patterns = [
        re.compile(r"\bpsycopg\.connect\b"),
        re.compile(r"\brequests\.(get|post|put|delete)\b"),
        re.compile(r"\bsocket\.socket\b"),
        re.compile(r"\burllib\.request\b"),
        re.compile(r"\bhttp\.client\b"),
    ]

    guard_re = re.compile(r"(SkipTest|skipUnless|skipIf|setUpModule|@unittest\.skip|MIOS_" + r"TEST_LIVE|MIOS_" + r"TEST_DB)")

    bad = []

    # os.listdir reached only the top level, so a suite in a tests/ subdirectory
    # was never read -- agent-pipe keeps two of its own down there.
    for d in _scan(root, *search_dirs):
        for dirpath, dirnames, filenames in os.walk(d):
            dirnames[:] = [x for x in dirnames
                           if not x.startswith(".") and x not in ("__pycache__", "node_modules")]
            for f in filenames:
                if (f.startswith("test_") or f.startswith("test-") or f.endswith("_test.py")) and f.endswith(".py"):
                    path = os.path.join(dirpath, f)
                    try:
                        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                            content = fh.read()

                        has_live_call = False
                        for p in patterns:
                            if p.search(content):
                                has_live_call = True
                                break

                        if has_live_call:
                            if not guard_re.search(content):
                                rel = os.path.relpath(path, root).replace("\\", "/")
                                bad.append(f"{rel} calls live network/DB resource without a SkipTest/guard sentinel")
                    except OSError:
                        pass

    if bad:
        for b in bad:
            sys.stderr.write(f"    [hermeticity-drift] {b}\n")
        return 1

    return 0

def check_containerfile_pinned_clones() -> int:
    import os, sys

    root = os.environ.get("MIOS_DRIFT_ROOT", ".")
    unpinned = []
    read = 0

    for r, ds, fs in os.walk(root):
        ds[:] = [d for d in ds if d not in (".git", ".worktrees", ".devloop", "target", "node_modules", ".venv")]
        for f in fs:
            if "Containerfile" in f:
                path = os.path.join(r, f)
                try:
                    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                        for idx, line in enumerate(fh, 1):
                            if "git clone" in line and not line.strip().startswith("#"):
                                if "--branch" not in line and "--tag" not in line and "-b " not in line and "@" not in line:
                                    rel = os.path.relpath(path, root).replace("\\", "/")
                                    unpinned.append(f"{rel}:{idx} -> {line.strip()}")
                    read += 1
                except OSError:
                    pass

    if read < 5:
        sys.stderr.write("    only %d Containerfile(s) read -- the corpus is wrong, so "
                         "an empty result is not a pass\n" % read)
        return 1

    if unpinned:
        sys.stderr.write("    Unpinned git clone command(s) found in Containerfiles:\n")
        for u in unpinned:
            sys.stderr.write(f"      {u}\n")
        return 1
    print("%d Containerfile(s) read for unpinned git clone" % read)
    return 0

def check_replaceme_mount_substitution() -> int:
    import os, sys, re
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover
        import tomli as tomllib  # type: ignore

    root = os.environ.get("MIOS_DRIFT_ROOT", ".")
    justfile = os.path.join(root, "Justfile")
    _rc = _absent(root, justfile)
    if _rc is not None:
        return _rc

    with open(justfile, "r", encoding="utf-8") as f:
        content = f.read()

    # P1-4: `miosd artifact-build` renders a disk's account and credential into
    # its [deploy.formats.<f>] recipe, so mounting a format recipe raw ships a
    # disk without them. Only the shared recipe may be mounted raw, and no
    # mounted recipe may carry a REPLACE placeholder.
    rendered = set()
    ssot = os.path.join(root, "usr/share/mios/mios.toml")
    if os.path.isfile(ssot):
        with open(ssot, "rb") as fh:
            formats = ((tomllib.load(fh).get("deploy") or {}).get("formats") or {})
        for name, spec in formats.items():
            if isinstance(spec, dict) and spec.get("recipe"):
                rendered.add((os.path.basename(spec["recipe"]), name))

    recipe_blocks = re.split(r"\n(?=[a-zA-Z0-9_-]+:)", content)

    bad = []
    for block in recipe_blocks:
        lines = block.strip().split("\n")
        if not lines or ":" not in lines[0]:
            continue
        recipe_name = lines[0].split(":")[0].strip()
        block_text = "\n".join(lines[1:])

        mounted_configs = re.findall(r"-v\s+\.?/?config/artifacts/([a-zA-Z0-9_.-]+\.toml)", block_text)
        for cfg in mounted_configs:
            for base, fmt in sorted(rendered):
                if base == cfg:
                    bad.append(f"Recipe '{recipe_name}' mounts the [deploy.formats.{fmt}] recipe '{cfg}' raw, "
                               f"so the disk gets no account or credential -- build it with "
                               f"`miosd artifact-build {fmt}`")
            cfg_path = os.path.join(root, "config/artifacts", cfg)
            if os.path.isfile(cfg_path):
                with open(cfg_path, "r", encoding="utf-8", errors="ignore") as cf:
                    cfg_text = cf.read()
                if "REPLACE" in cfg_text:
                    bad.append(f"Recipe '{recipe_name}' mounts '{cfg}', which carries a REPLACE placeholder")
        if re.search(r"sed\b[^\n]*REPLACE", block_text):
            bad.append(f"Recipe '{recipe_name}' sed-substitutes a credential placeholder; "
                       f"credentials are rendered by miosd artifact-build")

    if bad:
        for b in bad:
            sys.stderr.write(f"    [replaceme-drift] {b}\n")
        return 1

    return 0

def check_bib_rootfs_label_policy() -> int:
    import os, sys, re

    root = os.environ.get("MIOS_DRIFT_ROOT", ".")
    justfile = os.path.join(root, "Justfile")
    _rc = _absent(root, justfile)
    if _rc is not None:
        return _rc

    with open(justfile, "r", encoding="utf-8") as f:
        content = f.read()

    recipe_blocks = re.split(r"\n(?=[a-zA-Z0-9_-]+:)", content)

    valid_fs = {"ext4", "xfs", "btrfs"}
    bad = []

    for block in recipe_blocks:
        lines = block.strip().split("\n")
        if not lines or ":" not in lines[0]:
            continue
        recipe_name = lines[0].split(":")[0].strip()
        if recipe_name.startswith("#"):
            continue
        block_text = "\n".join(ln for ln in lines[1:] if ":=" not in ln)

        if "{{BIB}}" in block_text or "bootc-image-builder" in block_text:
            if "--rootfs" not in block_text:
                bad.append(f"Recipe '{recipe_name}' calls BIB without mandatory --rootfs flag")
            else:
                match = re.search(r"--rootfs\s+([a-zA-Z0-9]+)", block_text)
                if not match or match.group(1) not in valid_fs:
                    fs = match.group(1) if match else "missing"
                    bad.append(f"Recipe '{recipe_name}' uses unapproved or missing rootfs type '{fs}' (must be ext4/xfs/btrfs)")

    if bad:
        for b in bad:
            sys.stderr.write(f"    [bib-rootfs-drift] {b}\n")
        return 1

    return 0

def check_smoke_manifest() -> int:
    """[testing.smoke_components] is closed over the SSOT it ships in (task_3464).

    Every probe key is a kind `mios-gate image-equivalence` knows, every file
    probe exists in the source tree (commands, paths and rpm packages are build
    products, asserted against the image instead), every rpm_sections entry and
    every sections.<s> overlay names a [packages] section, every phases.<p> a
    registered phase, every profiles.<q> a declared profile, and the floor holds
    at least [testing].min_smoke_components probes. An overlay naming nothing
    can never be selected, so its probes would never run.
    """
    import os, sys
    import tomllib

    root = os.environ.get("MIOS_DRIFT_ROOT", ".")
    toml_path = os.path.join(root, "usr/share/mios/mios.toml")
    if not os.path.isfile(toml_path):
        # A tracked deliverable. Its absence is the anomaly, not a
        # reason to report success.
        print('check_smoke_manifest: a required SSOT file is missing, so nothing was'
              ' compared', file=sys.stderr)
        return 1

    with open(toml_path, "rb") as f:
        data = tomllib.load(f)

    sc = data.get("testing", {}).get("smoke_components", {})
    if not sc:
        sys.stderr.write("    Missing [testing.smoke_components] table in mios.toml\n")
        return 1

    file_kinds = ("shims", "units", "python_entries")
    kinds = file_kinds + ("paths", "commands", "rpm_sections", "manpages")
    valid_manpages = {
        "usr/share/man/man1/mios.1",
        "usr/share/man/man5/mios.toml.5",
        "usr/share/man/man7/mios.7",
    }
    if data.get("variants", {}).get("entries"):
        valid_manpages.add("usr/share/man/man7/mios-variants.7")
    for v in data.get("verbs", {}).keys():
        valid_manpages.add(f"usr/share/man/man1/mios-{v}.1")
    packages = {k for k, v in data.get("packages", {}).items() if isinstance(v, dict)}
    phases = {p.get("name") for p in data.get("build", {}).get("phases", {}).get("list", [])}
    profiles = {k for k, v in data.get("profiles", {}).items() if isinstance(v, dict)}
    overlay_owner = {
        "sections": ("[packages] section", packages),
        "phases": ("registered phase in [build.phases].list", phases),
        "profiles": ("declared profile in [profiles]", profiles),
    }

    bad = []
    missing = []

    def probes(table, at):
        n = 0
        for key, val in table.items():
            if key not in kinds:
                bad.append(f"[testing.smoke_components{at}].{key} is not a probe kind"
                           f" (one of {', '.join(kinds)})")
                continue
            if not isinstance(val, list) or not all(isinstance(x, str) for x in val):
                bad.append(f"[testing.smoke_components{at}].{key} is not a list of strings")
                continue
            for item in val:
                if key in file_kinds and not os.path.exists(os.path.join(root, item)):
                    missing.append(item)
                elif key == "manpages" and item not in valid_manpages:
                    missing.append(f"{item} (not a declared or core manpage)")
                if key == "rpm_sections":
                    if item not in packages:
                        bad.append(f"[testing.smoke_components{at}].rpm_sections names {item!r},"
                                   " no [packages] section")
                        continue
                    n += len(data["packages"][item].get("pkgs", []))
                else:
                    n += 1
        return n

    floor = probes({k: v for k, v in sc.items() if k not in overlay_owner}, "")
    for group, (what, owners) in overlay_owner.items():
        tables = sc.get(group, {})
        if not isinstance(tables, dict):
            bad.append(f"[testing.smoke_components].{group} is not a table")
            continue
        for name, table in tables.items():
            if name not in owners:
                bad.append(f"[testing.smoke_components.{group}.{name}] names {name!r},"
                           f" no {what}")
            if not isinstance(table, dict):
                bad.append(f"[testing.smoke_components.{group}.{name}] is not a table")
                continue
            probes(table, f".{group}.{name}")

    minimum = data.get("testing", {}).get("min_smoke_components")
    if not isinstance(minimum, int):
        bad.append("[testing].min_smoke_components is absent; the floor has no minimum")
    elif floor < minimum:
        bad.append(f"the floor asserts {floor} component(s), below"
                   f" [testing].min_smoke_components {minimum} (grow-only)")

    for b in bad:
        sys.stderr.write(f"    {b}\n")
    if missing:
        sys.stderr.write(f"    Paths listed in [testing.smoke_components] missing from repo: {missing}\n")
    return 1 if bad or missing else 0

def check_usr_over_etc() -> int:
    import os, sys, subprocess

    root = os.environ.get("MIOS_DRIFT_ROOT", ".")
    # `except Exception: tracked = []` made a refusing git read as an empty
    # /etc. Filtered from the whole listing, so no tracked etc/ is still a pass.
    listed, _rc = _tracked(root)
    if listed is None:
        return _rc
    tracked = [f for f in listed if f.startswith("etc/")]

    usr_share = os.path.join(root, "usr/share")
    usr_lib = os.path.join(root, "usr/lib")

    exempt_prefixes = (
        "etc/containers/systemd/",
        "etc/wsl.conf",
        "etc/wsl-distribution.conf",  # WSL reads only /etc, like wsl.conf
        "etc/cockpit/",
        "etc/containers/",
        "etc/greenboot/",
        "etc/mios/",
        "etc/skel/",
        "etc/profile.d/",
    )

    violations = []
    for f in tracked:
        if f.startswith(exempt_prefixes) or ".d/" in f or ".d" in os.path.basename(f):
            continue
        rel = f[4:]
        match_share = os.path.join(usr_share, rel)
        match_lib = os.path.join(usr_lib, rel)
        if os.path.isfile(match_share) or os.path.isfile(match_lib):
            violations.append(f"{f} shadows USR SSOT file ({match_share if os.path.isfile(match_share) else match_lib})")

    if violations:
        for v in violations:
            sys.stderr.write(f"    {v}\n")
        return 1
    return 0

def check_projection_registry() -> int:
    import os, sys, re
    import tomllib

    root = os.environ.get("MIOS_DRIFT_ROOT", ".")
    toml_path = os.path.join(root, "usr/share/mios/mios.toml")
    drift_script = os.path.join(root, "automation/98-drift-checks.sh")

    if not (os.path.isfile(toml_path) and os.path.isfile(drift_script)):
        # A tracked deliverable. Its absence is the anomaly, not a
        # reason to report success.
        print('check_projection_registry: a required SSOT file is missing, so nothing was'
              ' compared', file=sys.stderr)
        return 1

    with open(drift_script, "r", encoding="utf-8") as f:
        drift_code = f.read()

    with open(toml_path, "rb") as f:
        data = tomllib.load(f)

    surfaces = data.get("laws", {}).get("projection_registry", {}).get("surfaces", [])
    violations = []

    for s in surfaces:
        gen = s.get("generator", "")
        chk = s.get("check", "")
        if gen and not os.path.exists(os.path.join(root, gen)):
            violations.append(f"Projection generator '{gen}' missing from disk")
        if chk and not re.search(rf"^{chk}\s*\(\)", drift_code, re.MULTILINE):
            violations.append(f"Projection check function '{chk}' missing from 98-drift-checks.sh")

    if violations:
        for v in violations:
            sys.stderr.write(f"    {v}\n")
        return 1

    return 0

def check_bib_config_mount() -> int:
    import os, sys, re, glob
    import tomllib

    root = os.environ.get("MIOS_DRIFT_ROOT", ".")
    justfile = os.path.join(root, "Justfile")
    _rc = _absent(root, justfile)
    if _rc is not None:
        return _rc

    toml_files = glob.glob(os.path.join(root, "config/artifacts/*.toml"))
    bad = []

    for tf in toml_files:
        try:
            with open(tf, "rb") as f:
                tomllib.load(f)
        except Exception as e:
            bad.append(f"Invalid TOML syntax in {os.path.basename(tf)}: {e}")

    with open(justfile, "r", encoding="utf-8") as f:
        content = f.read()

    recipe_blocks = re.split(r"\n(?=[a-zA-Z0-9_-]+:)", content)

    for block in recipe_blocks:
        lines = block.strip().split("\n")
        if not lines:
            continue
        header_line = lines[0].strip()
        if header_line.startswith("#") or ":" not in header_line:
            continue
        recipe_name = header_line.split(":")[0].strip()
        if not re.match(r"^[a-zA-Z0-9_-]+$", recipe_name):
            continue
        block_text = "\n".join(lines[1:])

        if "{{BIB}}" in block_text or "bootc-image-builder" in block_text:
            config_mounts = re.findall(r"-v\s+\S+:/config\.(toml|json)", block_text)
            if len(config_mounts) != 1:
                bad.append(f"Recipe '{recipe_name}' must mount exactly ONE /config.toml (found {len(config_mounts)})")

    if bad:
        for b in bad:
            sys.stderr.write(f"    [bib-config-drift] {b}\n")
        return 1

    return 0

def check_win11_vm_template_xml() -> int:
    import os, sys, xml.etree.ElementTree as ET
    import tomllib

    root = os.environ.get("MIOS_DRIFT_ROOT", ".")
    xml_path = os.path.join(root, "tools/win11-secureboot-template.xml")
    ssot_path = os.path.join(root, "usr/share/mios/mios.toml")

    if len(_scan(root, xml_path, ssot_path)) < 2:
        return 0

    bad = []

    try:
        tree = ET.parse(xml_path)
        root_elem = tree.getroot()
    except Exception as e:
        bad.append(f"tools/win11-secureboot-template.xml is not well-formed XML: {e}")
        sys.stderr.write(f"    [win11-xml-drift] {bad[0]}\n")
        return 1

    try:
        with open(ssot_path, "rb") as f:
            data = tomllib.load(f)
        vm_cfg = data.get("vm", {}).get("win11", {})
        ssot_mem = str(vm_cfg.get("memory_kib", 25165824))
        ssot_vcpu = str(vm_cfg.get("vcpus", 12))

        mem_elem = root_elem.find("memory")
        vcpu_elem = root_elem.find("vcpu")

        if mem_elem is not None and mem_elem.text.strip() != ssot_mem:
            bad.append(f"Memory in template ({mem_elem.text.strip()}) does not match [vm.win11].memory_kib SSOT ({ssot_mem})")
        if vcpu_elem is not None and vcpu_elem.text.strip() != ssot_vcpu:
            bad.append(f"vCPUs in template ({vcpu_elem.text.strip()}) does not match [vm.win11].vcpus SSOT ({ssot_vcpu})")
    except Exception as e:
        bad.append(f"Failed to validate SSOT projection: {e}")

    if bad:
        for b in bad:
            sys.stderr.write(f"    [win11-xml-drift] {b}\n")
        return 1

    return 0

def check_db_seed_coverage() -> int:
    import os, sys, importlib.util
    import tomllib

    root = os.environ.get("MIOS_DRIFT_ROOT", ".")
    toml_path = os.path.join(root, "usr/share/mios/mios.toml")
    seed_script = os.path.join(root, "usr/libexec/mios/seed-db-config.py")

    for label, path in (("SSOT file", toml_path), ("db seeder script", seed_script)):
        if not os.path.isfile(path):
            sys.stderr.write(f"    Missing {label}: {path}\n")
            return 1

    try:
        with open(toml_path, "rb") as f:
            data = tomllib.load(f)
    except Exception as e:
        sys.stderr.write(f"    Failed to parse mios.toml: {e}\n")
        return 1

    # One failure path, not four: an unloadable spec, a module that raises on
    # import and a missing entry point are one outcome to the caller.
    spec = importlib.util.spec_from_file_location("seed_db_config", seed_script)
    seed_mod = importlib.util.module_from_spec(spec) if spec and spec.loader else None
    try:
        spec.loader.exec_module(seed_mod)
        get_seeded_sections = seed_mod.get_seeded_sections
    except Exception as e:
        sys.stderr.write(f"    Failed to import get_seeded_sections from {seed_script}: {e}\n")
        return 1

    seeded_set = set(get_seeded_sections(data))
    handled_separately = {"verbs", "packages"}

    # The allowlist is checked BOTH ways: a name only in the SSOT never reaches
    # the database; a name only in the allowlist is a rotted entry from a dropped
    # table. An absent or empty allowlist is reported, or a rename goes vacuous.
    known = getattr(seed_mod, "_CANONICAL_SECTIONS", None)
    if not isinstance(known, (set, frozenset)) or not known:
        sys.stderr.write(f"    _CANONICAL_SECTIONS absent or empty in {seed_script};"
                         f" the allowlist half of this check would prove nothing\n")
        return 1
    uncovered = [f"Section '{s}' is not handled by seed-db-config.py"
                 for s in data if s not in seeded_set and s not in handled_separately]
    uncovered += [f"Section '{s}' is listed in seed-db-config.py but mios.toml no"
                  f" longer has it" for s in sorted(known) if s not in data]
    if uncovered:
        for u in uncovered:
            sys.stderr.write(f"    {u}\n")
        return 1

    return 0

def check_account_column_parity() -> int:
    import os, sys, re

    root = os.environ.get("MIOS_DRIFT_ROOT", ".")
    schema_path = os.path.join(root, "usr/share/mios/postgres/schema-init.sql")
    _rc = _absent(root, schema_path)
    if _rc is not None:
        return _rc

    with open(schema_path, "r", encoding="utf-8") as f:
        schema_code = f.read()

    match = re.search(r"CREATE TABLE (?:IF NOT EXISTS )?account \((.*?)\);", schema_code, re.DOTALL | re.IGNORECASE)
    columns = set()
    if match:
        lines = match.group(1).splitlines()
        for line in lines:
            line_clean = line.strip()
            if line_clean and not line_clean.startswith("--") and not line_clean.upper().startswith("CONSTRAINT") and not line_clean.upper().startswith("PRIMARY"):
                col_name = line_clean.split()[0].strip('"')
                columns.add(col_name)

    alter_matches = re.findall(r"ALTER TABLE account ADD COLUMN (?:IF NOT EXISTS )?(\w+)", schema_code, re.IGNORECASE)
    columns.update(alter_matches)

    required_columns = {"name", "password_hash", "uid", "gid", "display", "home_dir", "shell", "groups", "is_admin", "enabled"}

    missing_in_schema = required_columns - columns

    viol = []
    if missing_in_schema:
        viol.append(f"Account schema missing column(s) required by consumer projections: {sorted(list(missing_in_schema))}")

    if viol:
        for v in viol:
            sys.stderr.write(f"    {v}\n")
        return 1

    return 0

def check_v2v_import_ssot() -> int:
    import os, sys, re, subprocess
    import tomllib

    root = os.environ.get("MIOS_DRIFT_ROOT", ".")
    wrapper = os.path.join(root, "usr/libexec/mios/mios-v2v-import")
    toml_path = os.path.join(root, "usr/share/mios/mios.toml")

    if not (os.path.isfile(wrapper) and os.path.isfile(toml_path)):
        # A tracked deliverable. Its absence is the anomaly, not a
        # reason to report success.
        print('check_v2v_import_ssot: a required SSOT file is missing, so nothing was'
              ' compared', file=sys.stderr)
        return 1

    with open(toml_path, "rb") as f:
        data = tomllib.load(f)
    fmt = data.get("virt", {}).get("v2v", {}).get("output_format")
    if not fmt:
        sys.stderr.write("    [virt.v2v].output_format is absent, so the wrapper has no SSOT value to resolve\n")
        return 1

    def planned(ssot):
        proc = subprocess.run(["bash", wrapper, "--dry-run"], capture_output=True, text=True,
                              env=dict(os.environ, MIOS_TOML=ssot))
        return proc.stdout + proc.stderr

    bad = []
    if f"-of {fmt}" not in planned(toml_path):
        bad.append(f"mios-v2v-import --dry-run output does not contain expected '-of {fmt}' from SSOT")
    # Echoing the shipped value proves nothing alone: a wrapper that hardcodes
    # today's "qcow2" emits it too, and passed. Hand it a probe SSOT whose values
    # are new and require each one back on its flag.
    import tempfile
    probe = {"output_format": ("-of", "negprobe-fmt"), "output_storage": ("-os", "negprobe-pool")}
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "mios.toml")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("[virt.v2v]\n" + "".join(f'{k} = "{v}"\n' for k, (_, v) in probe.items()))
        out = planned(p)
    bad += [f"mios-v2v-import ignores [virt.v2v].{k}: a probe SSOT set it to '{v}' and '{flag} {v}' never appeared"
            for k, (flag, v) in probe.items() if f"{flag} {v}" not in out]
    for b in bad:
        sys.stderr.write(f"    {b}\n")
    return 1 if bad else 0

def check_value_aliases() -> int:
    import sys, subprocess, os
    if len(sys.argv) >= 4:
        snap, tsv = sys.argv[2], sys.argv[3]
        root = os.environ.get("MIOS_DRIFT_ROOT", ".")
    else:
        root = os.environ.get("MIOS_DRIFT_ROOT", ".")
        snap = os.path.join(root, "usr/libexec/mios/mios-env-snapshot")
        tsv = os.path.join(root, "usr/share/mios/reference/value-aliases.tsv")

    # Both are tracked deliverables; absence is a missing subject, not a pass.
    missing = [p for p in (snap, tsv) if not os.path.isfile(p)]
    if missing:
        print("value-alias subject missing: %s" % ", ".join(
            _under(p, root) for p in missing))
        return 1

    env = {}
    snap_rel = os.path.relpath(snap, root).replace("\\", "/")
    vendor_rel = os.path.relpath(os.path.join(root, "usr/share/mios/mios.toml"), root).replace("\\", "/")
    sub_env = dict(os.environ, MIOS_ROOT=".", MIOS_DRIFT_ROOT=".", MIOS_VENDOR_TOML=vendor_rel, MIOS_MIGRATION_USE_RUST_RESOLVER_SHELL="false")
    try:
        proc = subprocess.run(["bash", snap_rel], cwd=root, capture_output=True, text=True, env=sub_env)
    except OSError as exc:
        print("mios-env-snapshot could not be run: %s" % exc)
        return 1
    # Returned 0 here, so a resolver that crashed read as "consistency verified".
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        print("mios-env-snapshot exited %d -- no alias corpus was produced: %s"
              % (proc.returncode, detail[-1] if detail else "no message"))
        return 1
    for line in proc.stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            env[k] = v
    bad = []
    rows = _alias_rows(tsv)
    sources = None
    if any(disp == "keep-distinct" for *_, disp, _r in rows):
        sources, why = _value_sources(os.path.abspath(root))
        if sources is None:
            print("keep-distinct rows cannot be verified: " + why)
            return 1
    seen = {}
    for lineno, a, b, disp, reason in rows:
        pair = frozenset((a, b))
        if pair in seen:
            bad.append(f"line {lineno}: {a} / {b} is already registered on line {seen[pair]} -- one pair, one row")
        seen.setdefault(pair, lineno)
        if a not in env or b not in env:  # never just skipped: that hid stranded keys; "X_" names a family
            bad += [f"{n} is registered ({a} -> {b}, {disp}) but the resolver does not emit it -- its consumers"
                    f" take their inline defaults; restore its key to the SSOT table that emits it"
                    for n in (a, b) if n not in env and not n.endswith("_")]
            continue
        va, vb = env[a], env[b]
        if disp in ("derive", "delete"):
            if va != vb:
                bad.append(f"{a}={va!r} != {b}={vb!r} (disposition={disp}: MUST be equal -- silent SSOT divergence)")
        elif disp == "keep-distinct":
            # task_942. keep-distinct means DISTINCT FACTS: two declarations, never
            # collapsed, whose values may coincide. One declaration under two
            # names is an alias whatever the row claims, and a claim nobody can
            # review is not a classification.
            sa, sb = sources.get(a), sources.get(b)
            if len(reason) < KEEP_DISTINCT_MIN_REASON:
                bad.append(f"line {lineno}: {a} / {b} is keep-distinct without a reason -- say why"
                           f" either can change without the other becoming wrong")
            if sa and sa == sb:
                bad.append(f"{a} and {b} both carry {sa} -- one declaration under two names is an alias"
                           f" (derive), never keep-distinct")
            elif va == vb and not (sa and sb):
                bad.append(f"{a} == {b} == {va!r} but {a if not sa else b} traces to no SSOT declaration,"
                           f" so nothing shows the two are distinct facts")
    for msg in bad:
        sys.stderr.write("    [value-alias-drift] " + msg + "\n")
    return 1 if bad else 0

def check_negatives_are_effective() -> int:
    import sys, re, os

    root = os.environ.get("MIOS_DRIFT_ROOT", ".")
    neg_path = os.path.join(root, "tests/drift-gate-negatives.sh")

    _rc = _absent(root, neg_path)
    if _rc is not None:
        return _rc

    with open(neg_path, encoding="utf-8", errors="ignore") as fh:
        content = fh.read()

    fn_matches = list(re.finditer(r'^(test_[a-zA-Z0-9_]+)\(\)\s*\{', content, re.MULTILINE))
    ineffective = []

    for i, m in enumerate(fn_matches):
        fn_name = m.group(1)
        start_idx = m.start()
        end_idx = fn_matches[i+1].start() if i + 1 < len(fn_matches) else len(content)
        main_match = re.search(r'^\s*main\(\)\s*\{', content[start_idx:end_idx], re.MULTILINE)
        if main_match:
            end_idx = start_idx + main_match.start()

        body = content[start_idx:end_idx]

        body_no_comments = re.sub(r'#.*$', '', body, flags=re.MULTILINE)
        body_no_logs = re.sub(r'\b(log|echo)\s+("[^"]*"|\'[^\']*\')', '', body_no_comments)

        # A test's OWN name is not evidence that it invokes anything: `test_check_foo`
        # used to satisfy the gate-invocation search purely because `check_foo` appears
        # in its definition line, certifying a body that asserts nothing. Search only
        # what follows the signature.
        _sig_end = body_no_logs.find('{')
        body_no_logs_body = body_no_logs[_sig_end + 1:] if _sig_end != -1 else body_no_logs

        has_die = bool(re.search(r'\b(die|exit\s+[1-9]|return\s+[1-9]|FAIL)\b', body_no_comments))
        has_gate_invoc = bool(re.search(
            r'(98-drift-checks\.sh|97-ssot-lint\.sh|tools/|automation/|usr/libexec/|usr/lib/mios/|check_[a-zA-Z0-9_]+|\b_[a-zA-Z0-9_]+_run\b|\b_[a-zA-Z0-9_]+_cmd\b|\b_[a-zA-Z0-9_]+_fail\b|\b_neg_gate\b)',
            body_no_logs_body
        ))

        if not (has_die and has_gate_invoc):
            ineffective.append(fn_name)

    if ineffective:
        for fn in ineffective:
            sys.stderr.write(f"    [ineffective-negative] {fn} lacks failure assertion or gate invocation\n")
        return 1

    return 0

def check_pipefail_grep_lint() -> int:
    import sys, re, os

    root = os.environ.get("MIOS_DRIFT_ROOT", ".")
    neg_path = os.path.join(root, "tests/drift-gate-negatives.sh")

    _rc = _absent(root, neg_path)
    if _rc is not None:
        return _rc

    with open(neg_path, encoding="utf-8", errors="ignore") as fh:
        lines = fh.readlines()

    bad = []
    for idx, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if "#" in stripped:
            stripped = stripped.split("#")[0]
        if "| grep" in stripped or "|grep" in stripped:
            left_side = stripped.split("|")[0].strip()
            if not re.search(r'\b(echo|printf)\b', left_side):
                bad.append((idx, stripped))

    if bad:
        for idx, l in bad:
            sys.stderr.write(f"    [pipefail-grep-violation] line {idx}: {l}\n")
        return 1

    return 0

def check_skip_list_covered() -> int:
    import fnmatch, os, sys
    import tomllib

    root = os.environ.get("MIOS_DRIFT_ROOT", ".")
    toml_path = os.path.join(root, "usr/share/mios/mios.toml")
    if not os.path.isfile(toml_path):
        # A tracked deliverable. Its absence is the anomaly, not a
        # reason to report success.
        print('check_skip_list_covered: a required SSOT file is missing, so nothing was'
              ' compared', file=sys.stderr)
        return 1

    viol = []
    with open(toml_path, "rb") as fh:
        globs = ((tomllib.load(fh).get("ci") or {}).get("globs") or {})

    # Nothing skipped is the goal, so an empty list passes. Every entry that is
    # there must say why, and must name an agent-pipe suite that exists.
    spec = globs.get("agent-pipe")
    if not isinstance(spec, dict) or not spec.get("dir") or not spec.get("glob"):
        viol.append("[ci.globs.agent-pipe] is absent or names no dir/glob -- the "
                    "agent-pipe suites would run in no tier")
        spec = {}
    skip = spec.get("skip") or []
    if skip and not str(spec.get("skip_reason", "")).strip():
        viol.append("[ci.globs.agent-pipe].skip carries no skip_reason")
    d, pat = spec.get("dir", ""), spec.get("glob", "")
    for fn in skip:
        if not (isinstance(fn, str) and fnmatch.fnmatchcase(fn, pat)
                and os.path.isfile(os.path.join(root, d, fn))):
            viol.append(f"[ci.globs.agent-pipe].skip names {fn!r}, which is no {d}/{pat} "
                        f"suite -- a stale skip would exempt the next suite given that name")

    for wf in (".github/workflows/mios-ci.yml", ".forgejo/workflows/build-mios.yml"):
        path = os.path.join(root, wf)
        if not os.path.isfile(path):
            continue
        if "SKIP=" in open(path, encoding="utf-8", errors="replace").read():
            viol.append(f"{wf} carries an inline SKIP= list, which shadows "
                        f"[ci.globs.agent-pipe].skip")

    if viol:
        sys.stdout.write("\n".join(viol) + "\n")
        return 1
    return 0

def check_template_self_conformance() -> int:
    import os, sys, subprocess, tempfile

    root = os.environ.get("MIOS_DRIFT_ROOT", ".")
    tmpl_dir = os.path.join(root, "usr/share/mios/templates")
    scaffold_script = os.path.join(root, "usr/libexec/mios/mios-new")
    conform_tool = os.path.join(root, "usr/libexec/mios/check-template-conformance")

    # All three are tracked deliverables (Law 16); returning 0 here asserted that
    # "every template scaffolds" while scaffolding none of them.
    subjects = ((tmpl_dir, os.path.isdir(tmpl_dir)),
                (scaffold_script, os.path.isfile(scaffold_script)),
                (conform_tool, os.path.isfile(conform_tool)))
    missing = [p for p, ok in subjects if not ok]
    if missing:
        print("template self-conformance subject missing: %s" % ", ".join(
            _under(p, root) for p in missing))
        return 1

    templates = [f for f in os.listdir(tmpl_dir) if not f.startswith(".") and os.path.isfile(os.path.join(tmpl_dir, f))]
    # A size guard covers every cause of an emptied corpus, not just deletion.
    if len(templates) < 20:
        print("template corpus is %d file(s); the canonical set is far larger, "
              "so this run examined an incomplete corpus" % len(templates))
        return 1
    failures = []

    # A conforming file is the deliverable; a zero exit is not. Same predicate
    # as check_template_conformance: MIOS_THEME_ROOT keeps SSOT and the
    # grandfather list on the real tree, --root walks only what was scaffolded.
    # One root per template -- match regexes anchor at the root, and the
    # `quadlet` umbrella and `quadlet-container` claim the same destination.
    scaffolded = 0
    walk_env = dict(os.environ, MIOS_THEME_ROOT=os.path.abspath(root),
                    MIOS_TOML_ROOT=os.path.abspath(root))
    for t in sorted(templates):
        if t in ("conformance-grandfathered.list", "PLACEHOLDERS.md"):
            continue
        with tempfile.TemporaryDirectory() as scaffold_root:
            # mios-new prefers installed templates; grade the tree's own.
            env = dict(os.environ, MIOS_DRIFT_CHECK_ROOT=scaffold_root,
                       MIOS_THEME_ROOT=scaffold_root,
                       MIOS_TEMPLATES_DIR=os.path.join(os.path.abspath(root),
                                                       "usr/share/mios/templates"))
            res = subprocess.run(
                [sys.executable, scaffold_script, t, "testmock"],
                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if res.returncode != 0:
                failures.append("Template %r failed to scaffold: %s"
                                % (t, (res.stderr or res.stdout or "").strip()
                                   or "exit %d, no diagnostic" % res.returncode))
                continue
            scaffolded += 1

            res = subprocess.run(
                [sys.executable, conform_tool, "--root", scaffold_root],
                env=walk_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True)
            if res.returncode == 0:
                continue
            out = ((res.stdout or "") + (res.stderr or "")).strip()
            detail = [ln.strip() for ln in out.splitlines()
                      if ln.strip() and ": Missing" in ln or "Out-of-order" in ln]
            if not detail:
                detail = [out or "exit %d, no diagnostic" % res.returncode]
            for ln in detail:
                failures.append("template %r scaffolds a non-conforming file: %s"
                                % (t, ln))

    if not scaffolded:
        print("no template scaffolded, so nothing was examined")
        return 1

    if failures:
        for f in failures:
            print("Violation:", f, file=sys.stderr)
        return 1
    return 0

def check_secret_handling() -> int:
    import os, sys, re

    root = os.environ.get("MIOS_DRIFT_ROOT", ".")
    key_regex = re.compile(r'-----BEGIN (?:RSA|OPENSSH|EC|PGP|PRIVATE)[A-Z ]*KEY[A-Z ]*-----\r?\n(?:[^\n]*\r?\n){0,6}?[A-Za-z0-9+/=]{40,}')  # task_965: a PEM header is not a key
    conn_regex = re.compile(r'(?:postgres|mysql|mongodb|redis)://[a-zA-Z0-9_-]+:[^@\s\"\'`]{4,}@')
    token_regex = re.compile(r'\b(?:AKIA[0-9A-Z]{16}|ghp_[a-zA-Z0-9]{36}|glpat-[a-zA-Z0-9_-]{20})\b')

    EXEMPT_PATHS = {
        "usr/share/doc/mios/reference/audit-security.md",
        "usr/share/doc/mios/reference/audit-deploy-plane.md",
        "tasks.jsonl",
    }

    violations = []

    for dirpath, dirnames, filenames in os.walk(root):
        # `.git` below root marks a nested checkout: another repo's source.
        if dirpath != root and (".git" in dirnames or ".git" in filenames):
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames if d not in (".git", ".worktrees", "__pycache__", ".cargo", ".rustup", "target", "node_modules", ".venv", ".agents", ".tmp.driveupload", "root")]
        for f in filenames:
            if f.endswith((".png", ".jpg", ".tar", ".zip", ".exe", ".pyc", ".iso", ".qcow2", ".vhdx", ".so", ".rlib", ".rmeta", ".dylib", ".dll", ".a", ".whl")):
                continue
            path = os.path.join(dirpath, f)
            rel = os.path.relpath(path, root).replace("\\", "/")
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                    content = fh.read()
            except Exception:
                continue
            # The exemptions below cover secret SHAPES, not this: tests/ included.
            if rel.endswith(".ps1") and "mios-secrets.env" in content:
                violations.append(f"{rel}: writes/reads secrets in plaintext %TEMP%\\mios-secrets.env")
            if rel in EXEMPT_PATHS or rel.startswith("tests/") or rel.startswith("scratch/") or rel.startswith(".agents/"):
                continue

            if key_regex.search(content):
                violations.append(f"{rel}: contains un-allowlisted Private Key block")
            if conn_regex.search(content):
                violations.append(f"{rel}: contains hardcoded database password connection string")
            if token_regex.search(content):
                violations.append(f"{rel}: contains hardcoded API secret token")

    if violations:
        for v in violations:
            sys.stderr.write(f"    {v}\n")
        return 1

    return 0

def check_os_update_timer_enabled() -> int:
    import os, sys, tomllib
    root = os.environ.get("MIOS_DRIFT_ROOT", ".")
    with open(os.path.join(root, "usr/share/mios/mios.toml"), "rb") as fh:
        ssot = tomllib.load(fh)
    pkgs = []
    def walk(o):
        if isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            pkgs.extend(p for p in o if isinstance(p, str))
    walk(ssot.get("packages", {}))
    return 0 if any(p in ("uupd", "bootc") for p in pkgs) else 1

def check_adhoc_toml_parsers() -> int:
    import os, re, sys
    root = os.environ.get("MIOS_DRIFT_ROOT", ".")
    EXEMPT = {"mios-common.ps1"}
    PATTERNS = [
        re.compile(r"\(\?s\)\s*\\\["),
        re.compile(r"\(\?ms\)\^\\s\*\\\["),
        re.compile(r"-match\s+'\^\\\[\(\.\+\)\\\]'"),
    ]
    viol = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in (".git", ".worktrees", ".devloop", "target", "node_modules", ".venv")]
        for fn in sorted(filenames):
            if not fn.endswith(".ps1") or fn in EXEMPT:
                continue
            p = os.path.join(dirpath, fn)
            try:
                with open(p, encoding="utf-8", errors="replace") as fh:
                    src = fh.read()
            except OSError:
                continue
            if any(pat.search(src) for pat in PATTERNS):
                rel = os.path.relpath(p, root).replace(os.sep, "/")
                viol.append(rel + " regex-parses mios.toml itself; call Get-MiosSsotValue from installation/mios-common.ps1 instead")
    if viol:
        print("\n".join(viol))
        return 1
    return 0

def check_install_uninstall_symmetry() -> int:
    import os, re, sys
    root = os.environ.get("MIOS_DRIFT_ROOT", ".")
    import tomllib as _toml

    toml_path = os.path.join(root, "usr/share/mios/mios.toml")
    uninst = os.path.join(root, "Uninstall-MiOS.ps1")
    viol = []
    if _toml is None:
        sys.stderr.write("[98-drift-checks]   WARNING: no tomllib/tomli -- skipping install/uninstall symmetry\n")
        return 0
    elif not os.path.isfile(uninst):
        viol.append("Uninstall-MiOS.ps1 is missing; the Windows install has no uninstaller")
    else:
        with open(toml_path, "rb") as fh:
            data = _toml.load(fh)
        owned = (data.get("windows", {}) or {}).get("owned_artifacts", {}) or {}
        if not owned:
            viol.append("mios.toml [windows.owned_artifacts] is empty; the uninstaller has no SSOT to be checked against")
        with open(uninst, encoding="utf-8", errors="replace") as fh:
            src = fh.read()

        sweeps = [re.compile(p) for p in re.findall(r"-match\s+'([^']*)'", src)]
        for glob in re.findall(r"-Filter\s+'([^']*)'", src):
            sweeps.append(re.compile(re.escape(glob).replace(r"\*", ".*")))

        def covered(name):
            return name in src or any(s.search(name) for s in sweeps)

        MECHANISM = {
            "task_names":     ("Unregister-ScheduledTask",),
            "service_names":  ("sc.exe delete", "Remove-Service"),
            "process_names":  ("Stop-Process",),
            "firewall_rules": ("Remove-NetFirewallRule",),
            "registry_roots": ("Remove-Item", "Remove-ItemProperty"),
            "shortcut_dirs":  ("Remove-Item",),
        }
        for field, verbs in MECHANISM.items():
            names = owned.get(field, []) or []
            if not names:
                continue
            if not any(v in src for v in verbs):
                viol.append("Uninstall-MiOS.ps1 has no %s removal step (none of %s) yet mios.toml declares %d in [windows.owned_artifacts].%s" % (field[:-1].replace("_", " "), "/".join(verbs), len(names), field))
            for name in names:
                if not covered(name):
                    viol.append("Uninstall-MiOS.ps1 never removes %s %r (declared in mios.toml [windows.owned_artifacts].%s)" % (field[:-1].replace("_", " "), name, field))
        # The other direction: an artifact the INSTALLER creates but nobody
        # declared has no SSOT entry, so the uninstaller cannot be checked
        # against it and this gate would never notice it exists.
        import subprocess
        CREATORS = {
            "task_names": r"Register-ScheduledTask\b[^\n]*?-TaskName\s+[\"']([^\"']+)[\"']",
            "service_names": r"(?:New-Service\b[^\n]*?-Name|sc\.exe\s+create)\s+[\"']?([A-Za-z0-9_.-]+)",
            "firewall_rules": r"New-NetFirewallRule\b[^\n]*?-DisplayName\s+[\"']([^\"']+)[\"']",
        }
        try:
            listed = subprocess.run(["git", "-C", root, "ls-files"],
                                    capture_output=True, text=True, check=False).stdout
        except OSError:
            listed = ""
        installers = []
        for rel in [x.strip() for x in listed.split("\n") if x.strip()]:
            base = os.path.basename(rel).lower()
            if not rel.lower().endswith((".ps1", ".psm1")):
                continue
            if ("install" in base or base.startswith("get-mios")
                    or "provision" in base or "bootstrap" in base):
                installers.append(rel)

        if len(installers) < 3:
            viol.append("only %d installer-shaped script(s) found; the subject list is "
                        "wrong, so an empty result is not a pass" % len(installers))
        else:
            for field, pat in CREATORS.items():
                rx = re.compile(pat, re.I)
                declared = {str(x).lower() for x in (owned.get(field) or [])}
                for rel in installers:
                    try:
                        with open(os.path.join(root, rel), encoding="utf-8",
                                  errors="ignore") as fh:
                            text = fh.read()
                    except OSError:
                        continue
                    for m in rx.finditer(text):
                        name = m.group(1)
                        if name.startswith("$"):
                            continue          # built from a variable; undetectable
                        if name.lower() not in declared:
                            viol.append("%s creates %s %r which mios.toml "
                                        "[windows.owned_artifacts].%s does not declare, so "
                                        "the uninstaller is never checked against it"
                                        % (rel, field[:-1].replace("_", " "), name, field))

    if viol:
        print("\n".join(viol))
        return 1
    return 0

def check_ps_port_fallback_ssot() -> int:
    import os, re, sys
    root = os.environ.get("MIOS_DRIFT_ROOT", ".")
    import tomllib as _toml

    if not os.path.isfile(os.path.join(root, "usr/share/mios/mios.toml")):
        # A tracked deliverable. Its absence is the anomaly, not a
        # reason to report success.
        print('check_ps_port_fallback_ssot: a required SSOT file is missing, so nothing was'
              ' compared', file=sys.stderr)
        return 1

    with open(os.path.join(root, "usr/share/mios/mios.toml"), "rb") as fh:
        ports = _toml.load(fh).get("ports", {}) or {}

    CALL = re.compile(r"Get-PortFromSsot\s+'[^']*'\s+'([a-z0-9_]+)'\s+(\d+)")
    ENTRY = re.compile(r"Key\s*=\s*'([a-z0-9_]+)'\s*;\s*Default\s*=\s*(\d+)")

    viol = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in (".git", ".worktrees", ".devloop", "target", "node_modules", ".venv")]
        for fn in sorted(filenames):
            if not fn.endswith(".ps1"):
                continue
            p = os.path.join(dirpath, fn)
            try:
                with open(p, encoding="utf-8", errors="replace") as fh:
                    src = fh.read()
            except OSError:
                continue
            rel = os.path.relpath(p, root).replace(os.sep, "/")
            for pat in (CALL, ENTRY):
                for key, literal in pat.findall(src):
                    want = ports.get(key)
                    if want is None:
                        viol.append("%s falls back on port key %r which does not exist in mios.toml [ports]" % (rel, key))
                    elif int(literal) != int(want):
                        viol.append("%s fallback %s=%s drifted from mios.toml [ports].%s=%s" % (rel, key, literal, key, want))
    if viol:
        print("\n".join(viol))
        return 1
    return 0

def check_ps_encoding_and_bom() -> int:
    import os, sys
    root = os.environ.get("MIOS_DRIFT_ROOT", ".")
    BOM = b"\xef\xbb\xbf"
    viol = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in (".git", ".worktrees", ".devloop", "target", "node_modules", ".venv")]
        for fn in sorted(filenames):
            if not fn.endswith(".ps1"):
                continue
            p = os.path.join(dirpath, fn)
            try:
                with open(p, "rb") as fh:
                    data = fh.read()
            except OSError:
                continue
            rel = os.path.relpath(p, root).replace(os.sep, "/")
            has_bom = data.startswith(BOM)
            body = data[len(BOM):] if has_bom else data
            non_ascii = any(b > 0x7F for b in body)
            # `irm | iex` hands 5.1 a string decoded as ISO-8859-1: a BOM becomes a token before param().
            entry = any(b"| iex" in ln and ("/" + fn).encode() in ln for ln in body.splitlines()[:40])
            if entry and (has_bom or non_ascii):
                viol.append(rel + " is an irm|iex entry point: it must be pure ASCII with no BOM (5.1 decodes it as ISO-8859-1)")
            elif non_ascii and not has_bom:
                viol.append(rel + " holds non-ASCII but has no UTF-8 BOM; Windows PowerShell 5.1 will read it as ANSI")
            elif has_bom and not non_ascii:
                viol.append(rel + " is pure ASCII yet carries a UTF-8 BOM; drop it")
    if viol:
        print("\n".join(viol))
        return 1
    return 0

def check_unit_security() -> int:
    import os, sys
    import tomllib as _toml

    root = os.environ.get("MIOS_DRIFT_ROOT", ".")

    systemd_dir = os.path.join(root, 'usr/lib/systemd/system')
    toml_path = os.path.join(root, 'usr/share/mios/mios.toml')

    unconfined_roster = set()
    if os.path.isfile(toml_path):
        with open(toml_path, 'rb') as fh:
            data = _toml.load(fh)
            sec = data.get('security', {}).get('privileged_units', {})
            unconfined_roster = set(sec.get('unconfined', []))

    required_directives = ['NoNewPrivileges', 'ProtectSystem', 'ProtectHome', 'PrivateTmp']
    viol = []

    if os.path.isdir(systemd_dir):
        for f in os.listdir(systemd_dir):
            if f.endswith('.service'):
                if f in unconfined_roster:
                    continue
                fp = os.path.join(systemd_dir, f)
                try:
                    with open(fp, encoding='utf-8', errors='replace') as fh:
                        content = fh.read()
                        missing = []
                        for directive in required_directives:
                            if directive not in content:
                                missing.append(directive)
                        if missing:
                            rel = os.path.relpath(fp, root).replace(os.sep, '/')
                            viol.append(f"{rel}: systemd service missing hardening directives ({', '.join(missing)})")
                except Exception: pass

    if viol:
        print('\n'.join(viol))
    return 0

def check_unit_dependency_closure() -> int:
    import os, sys, glob

    root = os.environ.get("MIOS_DRIFT_ROOT", ".")

    systemd_dir = os.path.join(root, 'usr/lib/systemd/system')
    quadlet_dir = os.path.join(root, 'usr/share/containers/systemd')

    known_units = set()
    if os.path.isdir(systemd_dir):
        for f in os.listdir(systemd_dir):
            if os.path.isfile(os.path.join(systemd_dir, f)):
                known_units.add(f)

    if os.path.isdir(quadlet_dir):
        for f in os.listdir(quadlet_dir):
            if f.endswith('.container'):
                base = f[:-10]
                known_units.add(f'{base}.service')
                known_units.add(f'{base}-service')
            elif f.endswith('.pod'):
                base = f[:-4]
                known_units.add(f'{base}-pod.service')
                known_units.add(f'{base}.pod')
            elif f.endswith('.volume'):
                base = f[:-7]
                known_units.add(f'{base}-volume.service')
            elif f.endswith('.network'):
                base = f[:-8]
                known_units.add(f'{base}-network.service')
            elif f.endswith('.image'):
                base = f[:-6]
                known_units.add(f'{base}-image.service')

    well_known = {
        'multi-user.target', 'network-online.target', 'network.target', 'default.target',
        'sockets.target', 'timers.target', 'syslog.target', 'local-fs.target', 'remote-fs.target',
        'basic.target', 'graphical.target', 'rescue.target', 'emergency.target', 'shutdown.target',
        'reboot.target', 'poweroff.target', 'podman.socket', 'podman.service', 'dbus.service',
        'dbus.socket', 'docker.service', 'docker.socket', 'containerd.service', 'systemd-journald.service',
        'systemd-resolved.service', 'systemd-networkd.service', 'time-sync.target', 'network-pre.target',
        'tailscaled.service', 'avahi-daemon.service', 'chronyd.service', 'firewalld.service',
        'nftables.service', 'sshd.service', 'sshd.socket', 'gdm.service', 'console-login-helper-messages.service',
        'nvidia-cdi-refresh.service', 'podman-restart.service', 'hermes-agent.service',
        'display-manager.service', 'akmods.service', 'pcsd.service', 'corosync.service',
        'pacemaker.service', 'k3s-agent.service', 'cryptsetup.target', 'redis.service',
        'sysinit.target', 'greenboot-healthcheck.service', 'ostree-remount.service',
        'ostree-prepare-root.service', 'waydroid-container.service', 'wslg-x11.service',
        'wslg-wayland.service', 'ceph.target', 'slices.target', 'graphical-session.target'
    }
    known_units.update(well_known)

    def is_valid_unit(u):
        if u in known_units: return True
        if u.endswith(('.mount', '.slice', '.swap')): return True
        if u.startswith(('systemd-', 'libvirtd', 'virt', 'cockpit', 'k3s-')): return True
        return False

    viol = []
    dirs_to_check = [systemd_dir, quadlet_dir]
    for d in _scan(root, *dirs_to_check):
        for root_dir, _, files in os.walk(d):
            for f in files:
                fp = os.path.join(root_dir, f)
                try:
                    with open(fp, encoding='utf-8', errors='replace') as fh:
                        for line in fh:
                            line = line.strip()
                            if line.startswith(('#', ';')): continue
                            for key in ('After=', 'Wants=', 'Requires=', 'Before=', 'BindsTo=', 'Requisite='):
                                if line.startswith(key):
                                    val = line[len(key):].strip()
                                    for token in val.split():
                                        token = token.strip()
                                        if token and not token.startswith('$') and not is_valid_unit(token):
                                            rel = os.path.relpath(fp, root).replace(os.sep, '/')
                                            viol.append(f"{rel}: dangling reference {key}{token}")
                except Exception: pass

    if viol:
        print('\n'.join(viol))
        return 1
    return 0

def check_docs_ratchet() -> int:
    import os, sys, glob
    root = os.environ.get("MIOS_DRIFT_ROOT", ".")
    sys.path.insert(0, os.path.join(root, "usr", "lib", "mios"))
    # mios_comments.py is a tracked deliverable and imports stdlib only, so an
    # import failure is the subject going missing, never an absent dependency.
    _rc = _absent(root, os.path.join(root, "usr/lib/mios/mios_comments.py"))
    if _rc is not None:
        if _rc:
            print("usr/lib/mios/mios_comments.py is gone, so no comment block was"
                  " classified and the ratchet counted nothing")
        return _rc
    try:
        import tomllib
        import mios_comments as mc
    except Exception as e:
        print("mios_comments.py is present but the docs ratchet could not load it"
              " (%s), so no comment block was ever classified" % e)
        return 1

    with open(os.path.join(root, "usr/share/mios/mios.toml"), "rb") as fh:
        data = tomllib.load(fh)
    docs = data.get("docs", {}) or {}
    pol = mc.Policy.from_toml(data)

    ceil_narr = docs.get("max_unmigrated_narrative")
    ceil_hint = docs.get("max_overlong_hints")
    ceil_stale = docs.get("max_stale_refs", 0)
    ceil_undoc = docs.get("max_undocumented_components", 16)
    viol = []
    if ceil_narr is None or ceil_hint is None or ceil_stale is None or ceil_undoc is None:
        viol.append("mios.toml [docs] is missing max_unmigrated_narrative/max_overlong_hints/max_stale_refs/max_undocumented_components"
                    " -- the ratchet has no floor and would pass vacuously")
        print("\n".join(viol))
        return 1

    try:
        refindex = mc.RefIndex.build(root)
    except RuntimeError as e:
        print("stale references were not measured, so that ceiling proves nothing: %s" % e)
        return 1
    ledger_path = os.path.join(root, "usr/share/mios/reference/manual-corpus.tsv")
    rows = {}
    if os.path.isfile(ledger_path):
        with open(ledger_path, encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("#") or not line.strip(): continue
                parts = line.rstrip("\n").split("\t")
                if len(parts) == 14:
                    rows[parts[5]] = dict(zip(["path","start_line","end_line","lines","words","sha12","class","reason","as","stale","landed_doc","landed_anchor","landed_words","pruned"], parts))

    def _landed(row):
        doc = row.get("landed_doc") or ""
        if not doc: return False
        p = os.path.join(root, doc.replace("/", os.sep))
        if not os.path.isfile(p): return False
        try:
            with open(p, encoding="utf-8", errors="replace") as fh: text = fh.read()
        except OSError: return False
        if ("mios-src:" + row["sha12"]) not in text: return False
        try:
            want = int(row.get("words") or 0)
            got = int(row.get("landed_words") or 0)
        except ValueError: return False
        return got >= pol.landing_min_word_ratio * want

    narr = hints = stale = 0
    for rel, full in mc.iter_source_files(root):
        try:
            blocks = mc.lex(full)
        except Exception:
            continue
        for b in blocks:
            b = mc.Block(**{**b.__dict__, "path": rel})
            v = mc.classify(b, pol, refindex)
            row = rows.get(b.sha12)
            if row is not None and _landed(row):
                continue
            if v.cls == "MIGRATE":
                narr += 1
            elif v.cls == "MIGRATE_HEADER":
                hints += 1
            if v.stale:
                stale += 1

    comp_files = glob.glob(os.path.join(root, "usr/libexec/mios/*")) + glob.glob(os.path.join(root, "automation/*.sh")) + glob.glob(os.path.join(root, "tools/*.py"))
    undoc = 0
    for f in comp_files:
        if not os.path.isfile(f): continue
        try:
            with open(f, "r", encoding="utf-8", errors="ignore") as fh:
                text = fh.read()
                if "AI-doc:" not in text and "AI-hint:" not in text:
                    undoc += 1
        except OSError:
            pass

    if narr > ceil_narr:
        viol.append("unmigrated narrative comment blocks %d > ceiling %d --"
                    " harvest them into docs, do NOT raise [docs].max_unmigrated_narrative"
                    % (narr, ceil_narr))
    if hints > ceil_hint:
        viol.append("over-cap AI-hint headers %d > ceiling %d --"
                    " shorten them, do NOT raise [docs].max_overlong_hints"
                    % (hints, ceil_hint))
    if stale > ceil_stale:
        viol.append("stale references %d > ceiling %d --"
                    " fix or remove stale references, do NOT raise [docs].max_stale_refs"
                    % (stale, ceil_stale))
    if undoc > ceil_undoc:
        viol.append("undocumented components %d > ceiling %d --"
                    " add AI-doc or AI-hint headers, do NOT raise [docs].max_undocumented_components"
                    % (undoc, ceil_undoc))
    print("[docs-ratchet] narrative=%d/%d overlong-hints=%d/%d stale-refs=%d/%d undoc-comp=%d/%d"
          % (narr, ceil_narr, hints, ceil_hint, stale, ceil_stale, undoc, ceil_undoc), file=sys.stderr)
    if viol:
        print("\n".join(viol))
        return 1
    return 0

def check_generator_host_parity() -> int:
    """Compatibility entry; native Rust owns generator source portability."""
    import subprocess
    wrapper = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "automation/98-drift-checks.sh")
    return subprocess.run(
        ["bash", wrapper, "check_generator_host_parity"],
        env=dict(os.environ, MIOS_DRIFT_CHECK_ROOT=os.environ.get("MIOS_DRIFT_ROOT", ".")),
        check=False,
    ).returncode


def check_doc_port_scheme() -> int:
    import os, sys, tomllib

    root = os.environ.get("MIOS_DRIFT_ROOT", ".")
    toml_path = os.path.join(root, "usr/share/mios/mios.toml")
    if not os.path.isfile(toml_path):
        # A tracked deliverable. Its absence is the anomaly, not a
        # reason to report success.
        print('check_doc_port_scheme: a required SSOT file is missing, so nothing was'
              ' compared', file=sys.stderr)
        return 1

    with open(toml_path, "rb") as fh:
        docs = tomllib.load(fh).get("docs", {}) or {}

    ret_ports = "|".join(str(p) for p in docs.get("retired_ports", []))
    port_clean = docs.get("port_clean", [])

    if not ret_ports:
        print("check_doc_port_scheme: [docs].retired_ports is empty or unreadable", file=sys.stderr)
        return 1

    import re
    viol = []
    pat = re.compile(rf"(^|[^0-9])({ret_ports})([^0-9]|$)")

    for f in port_clean:
        if not f:
            continue
        full_p = os.path.join(root, f)
        if not os.path.isfile(full_p):
            viol.append(f"[docs].port_clean names a missing file: {f}")
            continue
        try:
            with open(full_p, "r", encoding="utf-8", errors="ignore") as fh:
                for idx, line in enumerate(fh, 1):
                    if pat.search(line):
                        viol.append(f"retired port literal in {f}:{idx}: {line.strip()}")
        except OSError:
            pass

    # Code surface scanning (task_946 / LAW5-01):
    code_exemptions, scanned_exempt_hits = set(docs.get("retired_code_exemptions", [])), set()
    max_ex = docs.get("max_retired_code_exemptions")
    if max_ex is not None and len(code_exemptions) != max_ex:
        tag = "EXCEEDS ceiling" if len(code_exemptions) > max_ex else "is BELOW ceiling"
        viol.append(f"[docs].retired_code_exemptions count {len(code_exemptions)} {tag} {max_ex}" +
                    (" -- lower max_retired_code_exemptions to lock in progress" if len(code_exemptions) < max_ex else ""))
    for f in code_exemptions:
        if not os.path.isfile(os.path.join(root, f)):
            viol.append(f"[docs].retired_code_exemptions names a missing file: {f}")
    for c_dir in ("usr/libexec/mios", "usr/lib/mios"):
        full_c = os.path.join(root, c_dir)
        if not os.path.isdir(full_c):
            continue
        for dp, _, files in os.walk(full_c):
            if "__pycache__" in dp or ".git" in dp:
                continue
            for fname in files:
                full_path = os.path.join(dp, fname)
                rel_path = os.path.relpath(full_path, root).replace("\\", "/")
                try:
                    with open(full_path, "r", encoding="utf-8", errors="ignore") as fh:
                        for idx, line in enumerate(fh, 1):
                            m = pat.search(line)
                            if m:
                                if rel_path in code_exemptions:
                                    scanned_exempt_hits.add(rel_path)
                                else:
                                    viol.append(f"retired port {m.group(2)} in code file {rel_path}:{idx}: {line.strip()}")
                except OSError:
                    pass
    for sf in sorted(code_exemptions - scanned_exempt_hits):
        viol.append(f"[docs].retired_code_exemptions contains clean file {sf} -- remove it to shrink the register")
    if viol:
        for v in viol:
            print(v, file=sys.stderr)
        return 1
    return 0

def check_blade_reconcile_schema() -> int:
    import os, re, sys
    import tomllib

    root = os.environ.get("MIOS_DRIFT_ROOT", ".")
    toml_path = os.path.join(root, "usr/share/mios/mios.toml")
    sql_path = os.path.join(root, "usr/share/mios/postgres/schema-init.sql")
    if not os.path.isfile(toml_path):
        # A tracked deliverable. Its absence is the anomaly, not a
        # reason to report success.
        print('check_blade_reconcile_schema: a required SSOT file is missing, so nothing was'
              ' compared', file=sys.stderr)
        return 1
    with open(toml_path, "rb") as fh:
        data = tomllib.load(fh)

    rec = ((data.get("blade") or {}).get("reconcile") or {})
    if "enabled" not in rec:
        print("[blade.reconcile] has no `enabled` key -- an implied default is indistinguishable from a forgotten one, and this table decides whether partitioned writes are permitted")
        return 1

    RULE_KEYS = sorted(k for k in rec if k != "enabled")

    if not rec.get("enabled"):
        print("[blade-reconcile] divergence disabled; %d merge rule(s) declared, schema prerequisite not yet required" % len(RULE_KEYS))
        return 0

    viol = []
    sql = ""
    if os.path.isfile(sql_path):
        with open(sql_path, encoding="utf-8", errors="replace") as fh:
            sql = fh.read()
    for table in RULE_KEYS:
        m = re.search(r"CREATE TABLE IF NOT EXISTS\s+" + re.escape(table) + r"\s*\((.*?)\n\);", sql, re.S)
        if not m:
            viol.append("enabled = true but schema-init.sql declares no table '%s'" % table)
            continue
        body = m.group(1)
        if not re.search(r"\borigin_node\b", body):
            viol.append("table '%s' has no origin_node column, so a merged row cannot be attributed to the partition that wrote it" % table)
        if not re.search(r"\b(logical_ts|logical_clock)\b", body):
            viol.append("table '%s' has no logical_ts column, so append-ordered and last-writer-wins have nothing to order by" % table)
    if viol:
        viol.append("Land task_2239 (origin_node + logical_ts) or set [blade.reconcile].enabled = false until it does.")
        print("\n".join(viol))
        return 1
    return 0

_SUBCOMMAND_NAMES = (
    "agent-schema", "gate-registry",
    "firstboot-tier", "bound-image-store", "cephfs-ssot", "verb-stub-backends", "no-bare-port-literals",
    "globals-image-parity", "bake-plan-integrity", "negative-test-coverage",
    "structured", "drift-build-catalog", "drift-projection", "unwired-modules",
    "no-duplicate-value-key", "resolver-differential-parity", "legibility-ratchet",
    "legacy-archive",
    "header-integrity", "rbac-tiers", "ai-manifest", "capability-manifest",
    "surface-parity", "container-ports", "agent-pipe-budgets", "verb-backends",
    "dag-integrity",
    "ai-endpoint-local", "bake-refs-parity", "cli-eval-safety",
    "resolver-ssot-refs", "bake-budget", "greenboot", "router-intent-coverage",
    "council-gate-ssot", "test-hermeticity", "containerfile-pinned-clones",
    "replaceme-mount-substitution", "bib-rootfs-label-policy", "smoke-manifest",
    "usr-over-etc", "projection-registry",
    "bib-config-mount", "win11-vm-template-xml", "db-seed-coverage",
    "account-column-parity", "v2v-import-ssot", "value-aliases",
    "negatives-are-effective", "pipefail-grep-lint", "skip-list-covered",
    "template-self-conformance", "secret-handling",
    "os-update-timer-enabled", "adhoc-toml-parsers", "install-uninstall-symmetry",
    "ps-port-fallback-ssot", "ps-encoding-and-bom", "unit-security",
    "unit-dependency-closure", "docs-ratchet", "generator-host-parity",
    "doc-port-scheme", "blade-reconcile-schema",
)
SUBCOMMANDS = {k: globals()["check_" + k.replace("-", "_")] for k in _SUBCOMMAND_NAMES}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in SUBCOMMANDS:
        print("usage: drift-checks.py {%s}" % "|".join(sorted(SUBCOMMANDS)),
              file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(SUBCOMMANDS[sys.argv[1]]() or 0)
