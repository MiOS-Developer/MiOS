# AI-hint: Pure stdlib (ast) extractor of the server.py PUBLIC SURFACE for the refactor parity gate (refactor WS R0).
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md

from __future__ import annotations

import argparse
import ast
import json
import os
import stat
import tempfile
import sys
from typing import Any, NamedTuple

_ROUTE_METHODS = (
    "get", "post", "put", "delete", "patch", "head", "options",
    "websocket", "api_route", "trace",
)

_DYNAMIC = "<dynamic>"

def _const_str(node: ast.AST) -> str:
    """Return a string constant's value, or ``"<dynamic>"`` for a non-literal path."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return _DYNAMIC

def _route_from_decorator(dec: ast.AST) -> tuple[str, str] | None:
    """Map an ``@app.<method>("/path", ...)`` decorator to ``(METHOD, path)``.

    Returns ``None`` for any decorator that is not an ``app``-object route call.
    """
    if not isinstance(dec, ast.Call):
        return None
    func = dec.func
    if not isinstance(func, ast.Attribute):
        return None
    obj = func.value
    if not isinstance(obj, ast.Name) or obj.id != "app":
        return None
    method = func.attr.lower()
    if method not in _ROUTE_METHODS:
        return None
    path = _const_str(dec.args[0]) if dec.args else _DYNAMIC
    return method.upper(), path

def _kw_str(call: ast.Call, name: str) -> str | None:
    """Constant-string value of keyword ``name`` on a call.

    ``None`` when the keyword is absent, ``_DYNAMIC`` when it is present but not a
    string literal, else the literal value (an empty string is a real value).
    """
    for kw in call.keywords:
        if kw.arg == name:
            return _const_str(kw.value)
    return None

def _router_prefix_assign(node: ast.AST) -> tuple[str, list[str]] | None:
    if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
        return None
    callee = node.value.func
    if isinstance(callee, ast.Attribute):
        cname = callee.attr
    elif isinstance(callee, ast.Name):
        cname = callee.id
    else:
        return None
    if cname != "APIRouter":
        return None
    prefix = _kw_str(node.value, "prefix")
    if prefix is None:
        prefix = ""
    names = [t.id for t in node.targets if isinstance(t, ast.Name)]
    return (prefix, names) if names else None

def _include_router_call(node: ast.AST) -> tuple[str, str] | None:
    call = node.value if isinstance(node, (ast.Expr, ast.Assign)) else None
    if not isinstance(call, ast.Call):
        return None
    func = call.func
    if not (isinstance(func, ast.Attribute) and func.attr == "include_router"):
        return None
    if not (isinstance(func.value, ast.Name) and func.value.id == "app"):
        return None
    if not call.args or not isinstance(call.args[0], ast.Name):
        return None
    prefix = _kw_str(call, "prefix")
    if prefix is None:
        prefix = ""
    return call.args[0].id, prefix

def _router_decorator_candidate(dec: ast.AST) -> tuple[str, str, str] | None:
    if not isinstance(dec, ast.Call):
        return None
    func = dec.func
    if not isinstance(func, ast.Attribute) or not isinstance(func.value, ast.Name):
        return None
    obj = func.value.id
    if obj == "app":
        return None
    method = func.attr.lower()
    if method not in _ROUTE_METHODS:
        return None
    path = _const_str(dec.args[0]) if dec.args else _DYNAMIC
    return obj, method.upper(), path

def _compose_path(*segments: str) -> str:
    if any(seg == _DYNAMIC for seg in segments):
        return _DYNAMIC
    return "".join(segments)

def _imported_names(node: ast.AST) -> list[str]:
    out: list[str] = []
    if isinstance(node, ast.Import):
        for alias in node.names:
            out.append(alias.asname or alias.name.split(".")[0])
    elif isinstance(node, ast.ImportFrom):
        for alias in node.names:
            out.append(alias.asname or (alias.name if alias.name != "*" else "*"))
    return out

def project_surface(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    tree = ast.parse(src, filename=os.path.basename(path))

    routes: set[str] = set()
    provided: set[str] = set()
    routers: dict[str, str] = {}                     # router var -> its own prefix
    includes: dict[str, list[str]] = {}              # router var -> [mount prefixes]
    pending: list[tuple[str, str, str, str]] = []    # (obj, METHOD, dec path, handler)

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            provided.add(node.name)
            for dec in node.decorator_list:
                r = _route_from_decorator(dec)
                if r:
                    routes.add(f"{r[0]} {r[1]} -> {node.name}")
                cand = _router_decorator_candidate(dec)
                if cand:
                    pending.append((cand[0], cand[1], cand[2], node.name))
        elif isinstance(node, ast.ClassDef):
            provided.add(node.name)
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    provided.add(tgt.id)
                elif isinstance(tgt, (ast.Tuple, ast.List)):
                    for elt in tgt.elts:
                        if isinstance(elt, ast.Name):
                            provided.add(elt.id)
            ra = _router_prefix_assign(node)
            if ra:
                for nm in ra[1]:
                    routers[nm] = ra[0]
            inc = _include_router_call(node)
            if inc:
                includes.setdefault(inc[0], []).append(inc[1])
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.value is not None:
                provided.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            provided.update(_imported_names(node))
        elif isinstance(node, ast.Expr):
            inc = _include_router_call(node)
            if inc:
                includes.setdefault(inc[0], []).append(inc[1])

    for obj, method, dec_path, handler in pending:
        if obj not in routers:
            continue
        mount_prefixes = includes.get(obj) or [""]
        for mount in mount_prefixes:
            composed = _compose_path(mount, routers[obj], dec_path)
            routes.add(f"{method} {composed} -> {handler}")

    return {
        "routes": sorted(routes),
        "provided": sorted(provided),
        "counts": {"routes": len(routes), "provided": len(provided)},
    }

_MAX_NEST = 1

class _Scan(NamedTuple):
    routers: dict[str, str]                                          # router var -> own prefix
    router_routes: tuple[tuple[str, str, str, str], ...]            # (router var, METHOD, dec path, handler)
    app_includes: tuple[tuple[tuple[str, ...], str], ...]          # (include ref, mount prefix) via app.include_router
    nested_includes: tuple[tuple[str, tuple[str, ...], str], ...]  # (parent var, child ref, mount prefix)
    from_imports: dict[str, tuple[str, str]]                        # bound name -> (module dotted, original name)
    plain_imports: dict[str, str]                                   # bound name -> module dotted

def _include_ref(arg: ast.AST) -> tuple[str, ...]:
    if isinstance(arg, ast.Name):
        return ("name", arg.id)
    if isinstance(arg, ast.Attribute) and isinstance(arg.value, ast.Name):
        return ("attr", arg.value.id, arg.attr)
    return ("other",)

def _any_include_call(node: ast.AST) -> tuple[str, tuple[str, ...], str] | None:
    call = node.value if isinstance(node, (ast.Expr, ast.Assign)) else None
    if not isinstance(call, ast.Call):
        return None
    func = call.func
    if not (isinstance(func, ast.Attribute) and func.attr == "include_router"):
        return None
    if not isinstance(func.value, ast.Name) or not call.args:
        return None
    prefix = _kw_str(call, "prefix")
    if prefix is None:
        prefix = ""
    return func.value.id, _include_ref(call.args[0]), prefix

def _import_bindings(node: ast.AST) -> tuple[dict[str, tuple[str, str]], dict[str, str]]:
    fr: dict[str, tuple[str, str]] = {}
    pl: dict[str, str] = {}
    if isinstance(node, ast.ImportFrom):
        mod = node.module or ""
        for a in node.names:
            if a.name == "*":
                continue
            fr[a.asname or a.name] = (mod, a.name)
    elif isinstance(node, ast.Import):
        for a in node.names:
            pl[a.asname or a.name.split(".")[0]] = a.name
    return fr, pl

def _scan_module(tree: ast.Module) -> _Scan:
    """Collect the per-file structural facts ``project_package`` composes across
    files. Top-level only -- mirrors ``project_surface``'s scope (nested defs are
    not part of the route/router surface)."""
    routers: dict[str, str] = {}
    router_routes: list[tuple[str, str, str, str]] = []
    app_includes: list[tuple[tuple[str, ...], str]] = []
    nested_includes: list[tuple[str, tuple[str, ...], str]] = []
    from_imports: dict[str, tuple[str, str]] = {}
    plain_imports: dict[str, str] = {}

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                cand = _router_decorator_candidate(dec)
                if cand:
                    router_routes.append((cand[0], cand[1], cand[2], node.name))
            continue
        if isinstance(node, ast.Assign):
            ra = _router_prefix_assign(node)
            if ra:
                for nm in ra[1]:
                    routers[nm] = ra[0]
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            fr, pl = _import_bindings(node)
            from_imports.update(fr)
            plain_imports.update(pl)
            continue
        inc = _any_include_call(node)
        if inc:
            obj, ref, mount = inc
            if obj == "app":
                app_includes.append((ref, mount))
            else:
                nested_includes.append((obj, ref, mount))

    return _Scan(
        routers=routers,
        router_routes=tuple(router_routes),
        app_includes=tuple(app_includes),
        nested_includes=tuple(nested_includes),
        from_imports=from_imports,
        plain_imports=plain_imports,
    )

def _module_file(module: str, search_dir: str) -> str | None:
    if not module:
        return None

    if module.startswith("mios_pipe."):
        rel = module.replace(".", os.sep) + ".py"
        cand = os.path.join(search_dir, rel)
        if os.path.isfile(cand):
            return cand

    flat_name = module.split(".")[-1] + ".py"
    cand = os.path.join(search_dir, flat_name)
    if os.path.isfile(cand):
        try:
            with open(cand, encoding="utf-8") as f:
                content = f.read()
            import re
            m = re.search(r'sys\.modules\[__name__\]\s*=\s*_ShimModule\(__name__,\s*["\'](mios_pipe\.[^"\']+)["\']\)', content)
            if m:
                target = m.group(1)
                rel = target.replace(".", os.sep) + ".py"
                target_cand = os.path.join(search_dir, rel)
                if os.path.isfile(target_cand):
                    return target_cand
        except Exception:
            pass
        return cand
    return None

def _scan_file(path: str, cache: dict[str, "_Scan | None"]) -> "_Scan | None":
    """Parse + scan a file once, memoised by path. ``None`` when it is unreadable or
    unparsable -- a missing or broken sibling degrades to no routes, never raises."""
    if path in cache:
        return cache[path]
    scan: _Scan | None
    try:
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=os.path.basename(path))
        scan = _scan_module(tree)
    except (OSError, SyntaxError, ValueError):
        scan = None
    cache[path] = scan
    return scan

def _resolve_router_ref(ref: tuple[str, ...], scan: _Scan, this_file: str,
                        search_dir: str) -> tuple[str | None, str | None]:
    if ref[0] == "name":
        nm = ref[1]
        if nm in scan.routers:
            return this_file, nm
        if nm in scan.from_imports:
            mod, orig = scan.from_imports[nm]
            f = _module_file(mod, search_dir) or _module_file(orig, search_dir)
            if f:
                return f, orig
        return None, None
    if ref[0] == "attr":
        obj, attr = ref[1], ref[2]
        mod = scan.plain_imports.get(obj)
        if mod is None and obj in scan.from_imports:
            mod = scan.from_imports[obj][1]
        f = _module_file(mod or obj, search_dir)
        if f:
            return f, attr
        return None, None
    return None, None

def _collect_router_routes(file: str, var: str, prefix: str, budget: int,
                           visited: frozenset[tuple[str, str]], search_dir: str,
                           cache: dict[str, "_Scan | None"]) -> list[tuple[str, str, str]]:
    """Compose every route reachable through router ``var`` in ``file`` under the
    accumulated ``prefix``, following one in-budget nesting hop. Cycle- and
    depth-guarded (a revisit or an over-budget hop stops / collapses to ``_DYNAMIC``
    rather than recursing forever). Returns ``(METHOD, composed path, handler)``.
    """
    key = (file, var)
    if key in visited:
        return []                                   # cycle -> terminate, no fabrication
    scan = _scan_file(file, cache)
    if scan is None or var not in scan.routers:
        return []                                   # unresolved router -> no routes
    visited = visited | {key}
    here = _compose_path(prefix, scan.routers[var])
    out = [(m, _compose_path(here, dec), h)
           for (rv, m, dec, h) in scan.router_routes if rv == var]
    for parent, child_ref, mount in scan.nested_includes:
        if parent != var:
            continue
        cf, cv = _resolve_router_ref(child_ref, scan, file, search_dir)
        if cf is None or cv is None:
            continue                                # unresolved child -> no routes
        nb = budget - 1
        child_prefix = _compose_path(here, mount) if nb >= 0 else _DYNAMIC
        out.extend(_collect_router_routes(cf, cv, child_prefix, max(nb, 0),
                                          visited, search_dir, cache))
    return out

def project_package(entry_path: str, *, search_dir: str | None = None) -> dict[str, Any]:
    base = project_surface(entry_path)
    routes: set[str] = set(base["routes"])
    search_dir = search_dir or os.path.dirname(os.path.abspath(entry_path)) or "."
    cache: dict[str, _Scan | None] = {}
    entry_abs = os.path.abspath(entry_path)
    entry_scan = _scan_file(entry_abs, cache)
    if entry_scan is not None:
        for ref, mount in entry_scan.app_includes:
            if ref[0] == "name" and ref[1] in entry_scan.routers:
                continue                            # local router -> already composed in-file
            f, var = _resolve_router_ref(ref, entry_scan, entry_abs, search_dir)
            if f is None or var is None:
                continue                            # external / unresolved -> no fabrication
            for method, path, handler in _collect_router_routes(
                    f, var, mount, _MAX_NEST, frozenset(), search_dir, cache):
                routes.add(f"{method} {path} -> {handler}")

    out_routes = sorted(routes)
    return {
        "routes": out_routes,
        "provided": base["provided"],
        "counts": {"routes": len(out_routes), "provided": len(base["provided"])},
    }

def diff_surface(generated: dict[str, Any], committed: dict[str, Any]) -> list[str]:
    diffs: list[str] = []
    for key in ("routes", "provided"):
        gen = set(generated.get(key, []))
        com = set(committed.get(key, []))
        for removed in sorted(com - gen):
            diffs.append(f"{key}: REMOVED {removed!r} (in golden, gone from server.py)")
        for added in sorted(gen - com):
            diffs.append(f"{key}: ADDED {added!r} (in server.py, not in golden -- regenerate golden if intended)")
    return diffs

def _write_atomic(path: str, text: str) -> None:
    """Replace one regular output only after serialization and a complete durable write."""
    destination = os.path.abspath(path)
    payload = text.encode("utf-8")
    try:
        previous = os.lstat(destination)
    except FileNotFoundError:
        previous = None
    if previous is not None and not stat.S_ISREG(previous.st_mode):
        raise ValueError(f"output must be a regular file: {path}")
    if previous is not None:
        with open(destination, "rb") as fh:
            if fh.read() == payload:
                return
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb",
                                         dir=os.path.dirname(destination),
                                         prefix=".mios-surface-", delete=False) as fh:
            temporary = fh.name
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
            os.chmod(temporary, stat.S_IMODE(previous.st_mode) if previous else 0o644)
        os.replace(temporary, destination)
        temporary = None
    finally:
        if temporary is not None:
            os.unlink(temporary)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="mios_surface", allow_abbrev=False)
    parser.add_argument("target", metavar="server.py")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", metavar="golden.json")
    mode.add_argument("--out", metavar="golden.json")
    parser.add_argument("--package", action="store_true")
    parser.add_argument("--search-dir", metavar="dir")
    try:
        args = parser.parse_args(argv[1:])
        if args.search_dir is not None and not args.package:
            parser.error("--search-dir requires --package")
    except SystemExit as exc:
        return int(exc.code)
    try:
        if args.out is not None and os.path.realpath(args.out) == os.path.realpath(args.target):
            raise ValueError("output must not replace the projection source")
        proj = project_package(args.target, search_dir=args.search_dir) if args.package else project_surface(args.target)
        if args.check is not None:
            with open(args.check, encoding="utf-8") as fh:
                committed = json.load(fh)
            diffs = diff_surface(proj, committed)
            for difference in diffs:
                sys.stderr.write("    " + difference + "\n")
            return 1 if diffs else 0
        text = json.dumps(proj, indent=2, ensure_ascii=False) + "\n"
        if args.out is not None:
            _write_atomic(args.out, text)
        else:
            sys.stdout.write(text)
        return 0
    except (OSError, SyntaxError, ValueError, TypeError) as exc:
        sys.stderr.write(f"mios_surface: {exc}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
