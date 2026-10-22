#!/usr/bin/env python3
# AI-hint: Bake gate for Quickshell sources (automation/66-bake-quickshell.sh): fails when any function opens a Qt
# property update group (Qt::beginPropertyUpdateGroup()) and can return before closing it. A leaked group never
# closes, so every computed (bindable) property in the shell's GUI thread stops following its inputs -- in MiOS the
# layer-shell exclusive zones of the status island and the dock then freeze (0 after a reload) and Hyprland reserves
# nothing (operator 2026-10-17 "stuck in full screen-tiling"). Quickshell 0.2.1 (e.g. dacfa9d) has two such returns
# in HyprlandToplevel::updateFromObject(); upstream fixed them in 1bd5b083 (2026-03-17).
# AI-related: automation/66-bake-quickshell.sh, src/quickshell/patches/hyprland-toplevel-update-group.patch,
# usr/share/mios/quickshell/EdgeReveal.qml (runtime probe), tests/test-shell-reserve.py
"""Usage: check-update-groups.py SRC_DIR -- exit 0 when every update group is closed on all paths, 1 otherwise."""
import os
import re
import sys

BEGIN = re.compile(r"\bQt::beginPropertyUpdateGroup\s*\(\s*\)\s*;")
END = re.compile(r"\bQt::endPropertyUpdateGroup\s*\(\s*\)\s*;")
RETURN = re.compile(r"\breturn\b")


def strip(src: str) -> str:
    """Comments and string literals blanked (newlines kept for line numbers)."""
    out, i, n = [], 0, len(src)
    while i < n:
        if src.startswith("//", i):
            j = src.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i)); i = j
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append(re.sub(r"[^\n]", " ", src[i:j])); i = j
        elif src[i] in "\"'":
            q, j = src[i], i + 1
            while j < n and src[j] != q:
                j += 2 if src[j] == "\\" else 1
            j = min(j + 1, n)
            out.append(q + re.sub(r"[^\n]", " ", src[i + 1:j - 1]) + q if j - i >= 2 else src[i:j]); i = j
        else:
            out.append(src[i]); i += 1
    return "".join(out)


def leaks(path: str) -> list[str]:
    with open(path, encoding="utf-8", errors="replace") as f:
        src = strip(f.read())
    found = []
    for m in BEGIN.finditer(src):
        e = END.search(src, m.end())
        # the span a return could leave early: up to the matching end, or the end of the enclosing function
        depth, j = 0, m.end()
        stop = e.start() if e else len(src)
        while j < stop:
            c = src[j]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth < 0:   # the function (or block) closed before any end
                    stop = j
                    break
            j += 1
        if not e or e.start() > stop:
            found.append(f"{path}:{src.count(chr(10), 0, m.start()) + 1}: update group never closed")
            continue
        lambdas = lambda_spans(src, m.end(), stop)
        for r in RETURN.finditer(src, m.end(), stop):
            if any(a <= r.start() < b for a, b in lambdas):
                continue   # a lambda's own return (e.g. a setBinding body), not the function's
            found.append(f"{path}:{src.count(chr(10), 0, r.start()) + 1}: return inside an open property update group")
    return found


LAMBDA = re.compile(r"\[[^\[\]]*\]\s*(?:\([^()]*(?:\([^()]*\)[^()]*)*\))?\s*(?:mutable\s*)?(?:->\s*[\w:<>,\s*&]+?\s*)?\{")


def lambda_spans(src: str, start: int, stop: int) -> list[tuple[int, int]]:
    spans = []
    for m in LAMBDA.finditer(src, start, stop):
        depth, j = 1, m.end()
        while j < len(src) and depth:
            depth += {"{": 1, "}": -1}.get(src[j], 0)
            j += 1
        spans.append((m.end(), j))
    return spans


def main(root: str) -> int:
    bad = []
    for d, _, files in os.walk(root):
        for name in files:
            if name.endswith((".cpp", ".hpp", ".h", ".cc")):
                bad += leaks(os.path.join(d, name))
    for line in bad:
        print(line)
    return 1 if bad else 0


if __name__ == "__main__":
    if len(sys.argv) != 2 or not os.path.isdir(sys.argv[1]):
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1]))
