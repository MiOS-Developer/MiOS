# AI-hint: Two-sided test that every Quadlet bind of MiOS-owned state (/var/lib/mios, /srv) exists at boot: declared in usr/lib/tmpfiles.d or guarded by a ConditionPathExists at or under it.
# AI-related: /usr/share/containers/systemd, /usr/lib/tmpfiles.d, /usr/share/mios/mios.toml, tools/native/mios-gen/src/pod_quadlets.rs
# AI-functions: state_mounts, tmpfiles_declared, findings, main

import argparse
import glob
import os
import shutil
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OWNED = ("/var/lib/mios/", "/srv/")
TMPFILES_TYPES = {"d", "D", "v", "q", "Q", "f", "F", "C", "L"}  # every type that creates its path


def state_mounts(root):
    """[(rel_unit, host_source, conditions)] for every MiOS-owned bind source."""
    out = []
    pattern = os.path.join(root, "usr/share/containers/systemd/**/*.container")
    for path in sorted(glob.glob(pattern, recursive=True)):
        with open(path, encoding="utf-8") as fh:
            lines = [ln.strip() for ln in fh]
        conds = [ln.split("=", 1)[1].lstrip("!") for ln in lines if ln.startswith("ConditionPathExists=")]
        for ln in lines:
            if not ln.startswith("Volume="):
                continue
            src = ln[len("Volume="):].split(":")[0].rstrip("/")
            if src.startswith(OWNED) or src + "/" in OWNED:
                out.append((os.path.relpath(path, root), src, conds))
    return out


def tmpfiles_declared(root):
    declared = set()
    for path in glob.glob(os.path.join(root, "usr/lib/tmpfiles.d/*.conf")):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) >= 2 and parts[0].rstrip("!-+=~^") in TMPFILES_TYPES:
                    declared.add(parts[1].rstrip("/"))
    return declared


def findings(root):
    declared = tmpfiles_declared(root)
    bad = []
    for rel, src, conds in state_mounts(root):
        if "%" in src:
            # A template instance's path: no tmpfiles line can name it, so the
            # directory it lives in must exist; the leaf is the arming code's.
            parent = src[:src.index("%")].rstrip("/")
            if parent not in declared:
                bad.append(f"{rel}: binds {src}, whose parent {parent} no usr/lib/tmpfiles.d entry creates")
            else:
                print(f"NOT CHECKED: {rel}: per-instance leaf of {src} is created at arming time")
            continue
        guarded = any(c == src or c.startswith(src + "/") for c in conds)
        if src not in declared and not guarded:
            bad.append(f"{rel}: binds {src}, which no usr/lib/tmpfiles.d entry creates "
                       "and no ConditionPathExists guards")
    return bad


def _negative(root):
    """Drop the tmpfiles declaration of one declared, unguarded source in a
    scratch copy; the check must name it."""
    with tempfile.TemporaryDirectory() as tmp:
        for sub in ("usr/share/containers/systemd", "usr/lib/tmpfiles.d"):
            shutil.copytree(os.path.join(root, sub), os.path.join(tmp, sub))
        declared = tmpfiles_declared(tmp)
        target = next((src for _, src, conds in state_mounts(tmp)
                       if "%" not in src and src in declared and not any(c == src or c.startswith(src + "/") for c in conds)), None)
        if target is None:
            print("FAIL: no declared, unguarded state mount to plant against", file=sys.stderr)
            return 1
        for conf in glob.glob(os.path.join(tmp, "usr/lib/tmpfiles.d/*.conf")):
            with open(conf, encoding="utf-8") as fh:
                kept = [ln for ln in fh if ln.split()[1:2] != [target] and ln.split()[1:2] != [target + "/"]]
            with open(conf, "w", encoding="utf-8") as fh:
                fh.writelines(kept)
        if not any(f"binds {target}," in f for f in findings(tmp)):
            print(f"FAIL: undeclared {target} was not named", file=sys.stderr)
            return 1
    print(f"PASS negative: dropping the declaration of {target} is named")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=REPO)
    args = ap.parse_args()
    mounts = state_mounts(args.root)
    if not mounts:
        print("FAIL: no Quadlet binds MiOS-owned state; the scan reached nothing", file=sys.stderr)
        return 1
    bad = findings(args.root)
    for b in bad:
        print(f"FAIL: {b}", file=sys.stderr)
    if bad:
        return 1
    print(f"PASS positive: {len(mounts)} MiOS-owned state mount(s) exist at boot or are condition-guarded")
    return _negative(args.root)


if __name__ == "__main__":
    sys.exit(main())
