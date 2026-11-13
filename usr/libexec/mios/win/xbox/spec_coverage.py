#!/usr/bin/env python3
# AI-hint: mios-spec-01 spec-coverage gate: traces which SSOT keys an image pipeline reads and fails any applicable key it never reads unless [spec.coverage] declares it not applicable or pending; also checks [spec.parity].
# AI-related: usr/share/mios/mios.toml, usr/libexec/mios/win/xbox/render_spec.py, usr/libexec/mios/win/xbox/build.py, tests/test-mios-spec-coverage.py
"""Spec-coverage gate (mios-spec-01). Run: spec_coverage.py --check [--layer TOML ...]   (exit 0 covered, 1 violations)"""

from __future__ import annotations

import argparse
import copy
import os
import secrets
import sys
import tempfile
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import render_spec as R  # noqa: E402
import resolve_latest as RL  # noqa: E402
import spec_items as S  # noqa: E402
import answer as A  # noqa: E402

_TREE = R._TREE
GRANULARITIES = ("key", "section")
DECLARATIONS = ("not_applicable", "pending")
# trace-only placeholders: the gate never needs the operator's secrets (and never reads ~/.ssh)
TRACE_SSH_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIMiOSSpecCoverageTraceOnlyNotARealKey00000 trace@mios-spec"


class _Tracer:
    """Records the dotted SSOT paths a pipeline reads; reads inside a discovery scan are not uses."""

    def __init__(self) -> None:
        self.read: Set[str] = set()
        self.on = True

    def add(self, path: str) -> None:
        if self.on:
            self.read.add(path)


class Traced(dict):
    """A dict that reports item reads (and iteration, which consumes every entry) to its tracer."""
    __slots__ = ("_p", "_t")

    def __init__(self, src: dict, path: str, tracer: _Tracer):
        super().__init__()
        self._p, self._t = path, tracer
        for k, v in src.items():
            dict.__setitem__(self, k, _wrap(v, f"{path}.{k}" if path else str(k), tracer))

    def _rec(self, k: Any) -> None:
        self._t.add(f"{self._p}.{k}" if self._p else str(k))

    def __getitem__(self, k):
        self._rec(k)
        return dict.__getitem__(self, k)

    def get(self, k, default=None):
        self._rec(k)
        return dict.get(self, k, default)

    def __contains__(self, k):
        self._rec(k)
        return dict.__contains__(self, k)

    def _all(self) -> None:
        for k in dict.keys(self):
            self._rec(k)

    def items(self):
        self._all()
        return dict.items(self)

    def values(self):
        self._all()
        return dict.values(self)

    def __iter__(self):
        self._all()
        return dict.__iter__(self)

    def __deepcopy__(self, memo):
        new = Traced.__new__(Traced)
        dict.__init__(new)
        new._p, new._t = self._p, self._t
        for k, v in dict.items(self):
            dict.__setitem__(new, k, copy.deepcopy(v, memo))
        return new

    def __reduce__(self):
        return (dict, (dict(self),))


def _wrap(v: Any, path: str, tracer: _Tracer) -> Any:
    if isinstance(v, dict):
        return Traced(v, path, tracer)
    if isinstance(v, list):
        return [_wrap(x, f"{path}[]", tracer) if isinstance(x, dict) else x for x in v]
    return v


def _get(tree: dict, path: str) -> Any:
    node: Any = tree
    for k in path.split("."):
        if not isinstance(node, dict) or k not in node:
            return None
        node = dict.__getitem__(node, k) if isinstance(node, Traced) else node[k]
    return node


def leaves(node: Any, path: str) -> Iterable[str]:
    """Every scalar or array key under a table (arrays are values, not tables)."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield from leaves(v, f"{path}.{k}" if path else str(k))
    else:
        yield path


def trace_xbox(merged: dict, layers: List[str], tree_root: str = _TREE) -> Set[str]:
    """SSOT paths the MiOS-Xbox pipeline reads, render through SetupComplete. Secrets are trace-only placeholders;
    the operator home is empty."""
    base = copy.deepcopy(merged)
    base.setdefault("identity", {})["default_password"] = "Trace-" + secrets.token_urlsafe(18)
    base.setdefault("auth", {})["existing_ssh_key"] = TRACE_SSH_KEY
    tracer = _Tracer()
    traced = Traced(base, "", tracer)
    walk, home = R._walk_refs, R._operator_home

    def quiet_walk(x, *a):   # *_ref discovery lists every key: a scan, not a use
        tracer.on = False
        try:
            return iter(list(walk(x, *a)))
        finally:
            tracer.on = True

    with tempfile.TemporaryDirectory(prefix="mios-spec-coverage.") as empty_home:
        R._walk_refs, R._operator_home = quiet_walk, (lambda: empty_home)
        try:
            RL.load_config(traced)
            doc = R.render(traced, layers)
            S.items_from_spec(doc, tree_root)
            A.render_answer_file(doc)
            A.render_registry(doc)
            A.render_setupcomplete_cmd(doc)
        finally:
            R._walk_refs, R._operator_home = walk, home
    return tracer.read


TRACERS: Dict[str, Callable[..., Set[str]]] = {"mios-xbox": trace_xbox}


def _covered(path: str, reads: Set[str]) -> bool:
    return path in reads or any(r.startswith(path + ".") for r in reads)


def _under(path: str, prefixes: Iterable[str]) -> Optional[str]:
    return next((p for p in prefixes if path == p or path.startswith(p + ".")), None)


def plan(merged: dict) -> Tuple[dict, List[str]]:
    """Validate [spec.coverage] itself. Returns (normalised map, violations)."""
    bad: List[str] = []
    cov = ((merged.get("spec") or {}).get("coverage")) or {}
    images = set(_get(merged, str(cov.get("images_ref") or "")) or {})
    if not images:
        return {}, ["[spec.coverage].images_ref names no image table"]
    roots = cov.get("roots") or {}
    sections: Dict[str, Set[str]] = {}
    for path, spec in (cov.get("sections") or {}).items():
        names = spec.get("images") if isinstance(spec, dict) else None
        names = sorted(images) if names == "all" else names
        if not isinstance(names, list) or not names:
            bad.append(f"[spec.coverage.sections].{path}: images must be \"all\" or a non-empty list")
            continue
        unknown = sorted(set(names) - images)
        if unknown:
            bad.append(f"[spec.coverage.sections].{path}: unknown image(s) {unknown}")
        if not isinstance(_get(merged, path), dict):
            bad.append(f"[spec.coverage.sections].{path}: not an SSOT table")
        sections[path] = set(names) & images
    for root, gran in roots.items():
        if gran not in GRANULARITIES:
            bad.append(f"[spec.coverage.roots].{root}: granularity must be one of {GRANULARITIES}")
        node = _get(merged, root)
        if not isinstance(node, dict):
            bad.append(f"[spec.coverage.roots].{root}: not an SSOT table")
            continue
        if root not in sections:
            bad.append(f"spec section [{root}] has no [spec.coverage.sections] entry")
        if gran == "key":
            for k, v in node.items():
                if isinstance(v, dict) and f"{root}.{k}" not in sections:
                    bad.append(f"spec section [{root}.{k}] has no [spec.coverage.sections] entry (map it to its images)")
    decl: Dict[str, Dict[str, Dict[str, str]]] = {d: {} for d in DECLARATIONS}
    for kind in DECLARATIONS:
        for image, entries in (cov.get(kind) or {}).items():
            if image not in images:
                bad.append(f"[spec.coverage.{kind}].{image}: not an image")
                continue
            for path, why in (entries or {}).items():
                if not isinstance(why, str) or len(why.strip()) < 12:
                    bad.append(f"[spec.coverage.{kind}.{image}].\"{path}\": needs a reason")
                if _get(merged, path) is None:
                    bad.append(f"[spec.coverage.{kind}.{image}].\"{path}\": no such SSOT key")
                decl[kind].setdefault(image, {})[path] = str(why)
    gated = cov.get("gated")
    if not isinstance(gated, list) or not gated or any(not isinstance(i, str) or not i for i in gated):
        bad.append("[spec.coverage].gated must be a non-empty list of image names")
        gated = []
    for image in gated:
        if image not in images:
            bad.append(f"[spec.coverage].gated: {image} is not an image")
        if image not in TRACERS:
            bad.append(f"[spec.coverage].gated: no pipeline tracer for {image}")
    return {"images": images, "roots": roots, "sections": sections, "decl": decl, "gated": gated}, bad


def _section_of(path: str, sections: Dict[str, Set[str]]) -> Optional[str]:
    return max((s for s in sections if path == s or path.startswith(s + ".")), key=len, default=None)


def evaluate(merged: dict, cmap: dict, image: str, reads: Set[str]) -> Tuple[dict, List[str]]:
    """Coverage of one image: every applicable unit is read, or declared not applicable / pending. Declarations the
    pipeline contradicts (it reads the key) are violations too."""
    bad: List[str] = []
    na = cmap["decl"]["not_applicable"].get(image, {})
    pend = cmap["decl"]["pending"].get(image, {})
    counts = {"covered": 0, "not_applicable": 0, "pending": 0}
    units: List[str] = []
    for root, gran in cmap["roots"].items():
        node = _get(merged, root)
        if not isinstance(node, dict):
            continue
        if gran == "key":
            units += [p for p in leaves(node, root) if image in cmap["sections"].get(_section_of(p, cmap["sections"]) or "", set())]
        elif image in cmap["sections"].get(root, set()):
            units += [f"{root}.{k}" for k in node]   # each subtable as one unit; each own scalar on its own
    if not units:
        bad.append(f"{image}: no applicable coverage units are mapped to this gated image")
    for unit in units:
        if _covered(unit, reads):
            counts["covered"] += 1
        elif _under(unit, na):
            counts["not_applicable"] += 1
        elif _under(unit, pend):
            counts["pending"] += 1
        else:
            bad.append(f"{image}: {unit} applies to {image} but its pipeline never reads it -- render it, or declare it "
                       f"under [spec.coverage.not_applicable.{image}] / [spec.coverage.pending.{image}] with a reason")
    for kind, entries in (("not_applicable", na), ("pending", pend)):
        for path in entries:
            if not any(u == path or u.startswith(path + ".") for u in units):
                bad.append(f"{image}: [spec.coverage.{kind}.{image}].\"{path}\" declares a key that does not apply to {image}")
            elif any(_covered(u, reads) for u in units if u == path or u.startswith(path + ".")):
                bad.append(f"{image}: [spec.coverage.{kind}.{image}].\"{path}\" is stale -- the {image} pipeline reads it; "
                           "remove the declaration")
    return {**counts, "units": len(units), "pending_keys": sorted(pend)}, bad


def parity(merged: dict) -> List[str]:
    """[spec.parity].equal: shared identity values the Windows and Linux images must carry identically."""
    bad = []
    for pair in ((merged.get("spec") or {}).get("parity") or {}).get("equal") or []:
        a, b = (pair + [None, None])[:2] if isinstance(pair, list) else (None, None)
        if not a or not b:
            bad.append(f"[spec.parity].equal: {pair!r} is not a pair of SSOT keys")
            continue
        va, vb = _get(merged, a), _get(merged, b)
        if va is None or vb is None:
            bad.append(f"[spec.parity]: {a if va is None else b} is not an SSOT key")
        elif va != vb:
            bad.append(f"parity: {a} = {va!r} differs from {b} = {vb!r} (Windows and Linux images must match)")
    return bad


def check(merged: dict, layers: List[str], tree_root: str = _TREE, tracers: Optional[Dict[str, Callable[..., Set[str]]]] = None,
          required_image: Optional[str] = None
          ) -> Tuple[dict, List[str]]:
    """Map validity, coverage of every gated image and parity. Declared pending work remains
    unimplemented in the returned (report, violations)."""
    cmap, bad = plan(merged)
    report: Dict[str, Any] = {"images": {}}
    if not cmap:
        return report, bad
    if required_image is not None and required_image not in cmap["gated"]:
        bad.append(f"{required_image}: this pipeline requires its image in [spec.coverage].gated")
    active_tracers = TRACERS if tracers is None else tracers
    for image in cmap["gated"]:
        fn = active_tracers.get(image)
        if fn is None:
            bad.append(f"{image}: configured pipeline tracer is unavailable")
            continue
        try:
            reads = fn(merged, layers, tree_root)
        except (R.SpecError, RL.ResolveError, A.PayloadError, S.SpecError) as e:
            bad.append(f"{image}: its pipeline does not render from this SSOT ({e})")
            continue
        report["images"][image], ibad = evaluate(merged, cmap, image, reads)
        bad += ibad
    report["not_gated"] = sorted(cmap["images"] - set(cmap["gated"]))
    bad += parity(merged)
    return report, bad


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="spec_coverage.py", description=__doc__)
    ap.add_argument("--check", action="store_true", required=True)
    ap.add_argument("--layer", action="append", default=[], help="SSOT layer (default: the tree's vendor mios.toml)")
    ap.add_argument("--require-image", help="the caller's image must be gated, even if a layer changes the gated list")
    a = ap.parse_args(argv)
    layers = a.layer or [os.path.join(_TREE, "usr", "share", "mios", "mios.toml")]
    report, bad = check(R.mios_toml.load_merged(layers), layers, required_image=a.require_image)
    for image, r in report.get("images", {}).items():
        print(f"[spec-coverage] {image}: {r['covered']} covered, {r['not_applicable']} not applicable, "
              f"{r['pending']} pending of {r['units']} applicable")
    if report.get("not_gated"):
        print(f"[spec-coverage] not traced yet (UNRUN): {', '.join(report['not_gated'])}")
    for v in bad:
        print(f"[spec-coverage] VIOLATION {v}", file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
