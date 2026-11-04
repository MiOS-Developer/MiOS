#!/usr/bin/env python3
# AI-hint: Canonical AST surface, atomic projector CLI and declared native generation tests, plus real FastAPI route parity without application lifespan.
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md
"""Unit tests for mios_surface (refactor R0 surface-parity gate)."""

import contextlib
import io
import json
import os
import stat
import subprocess
import tomllib
from pathlib import Path
from unittest import mock
import shutil
import sys
import tempfile

import mios_surface as s

_fails = 0

_BEFORE = '''\
import os
import a.b.c as abc
from helpers import scrub, translate as _xlate
A_CONST = 1
B_CONST, C_CONST = 2, 3
TYPED: int = 4

@app.get("/health")
def health():
    return "ok"

@app.post("/v1/chat/completions")
async def chat_completions(req):
    return req

@app.middleware("http")
async def _mw(request, call_next):
    return await call_next(request)

def scrub(x):  # defined locally here, BEFORE the move
    return x

class Widget:
    def method(self):  # nested def must NOT appear as a top-level name
        return 1
'''

_AFTER_MOVE = _BEFORE.replace(
    "from helpers import scrub, translate as _xlate",
    "from helpers import translate as _xlate\nfrom mios_scrub import scrub",
).replace(
    "def scrub(x):  # defined locally here, BEFORE the move\n    return x\n\n\n", "",
)

def check(name, cond, detail=""):
    global _fails
    if not cond:
        _fails += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

def _project(src):
    fd, path = tempfile.mkstemp(suffix=".py")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(src)
        return s.project_surface(path)
    finally:
        os.unlink(path)

def _project_package(files, entry):
    """Write a synthetic multi-file package to a fresh temp dir, project the whole
    package from ``entry``, then remove the dir. ``files`` maps filename -> source.
    Uses an ephemeral ``tempfile`` dir (no fixed path baked in) and cleans up."""
    d = tempfile.mkdtemp()
    try:
        for name, src in files.items():
            with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
                fh.write(src)
        return s.project_package(os.path.join(d, entry))
    finally:
        shutil.rmtree(d, ignore_errors=True)

def t_routes():
    proj = _project(_BEFORE)
    routes = set(proj["routes"])
    check("route: GET /health -> health", "GET /health -> health" in routes)
    check("route: POST chat_completions", "POST /v1/chat/completions -> chat_completions" in routes)
    check("route: middleware is NOT a route", not any("_mw" in r for r in routes), routes)
    check("route: exactly 2 routes", proj["counts"]["routes"] == 2, str(proj["counts"]))
    check("route: sorted", proj["routes"] == sorted(proj["routes"]))

def t_provided():
    prov = set(_project(_BEFORE)["provided"])
    check("provided: top-level funcs", {"health", "chat_completions", "_mw", "scrub"} <= prov)
    check("provided: nested method excluded", "method" not in prov)
    check("provided: class", "Widget" in prov)
    check("provided: globals simple+tuple+annotated", {"A_CONST", "B_CONST", "C_CONST", "TYPED"} <= prov)
    check("provided: import asname (a.b.c as abc)", "abc" in prov)
    check("provided: plain import binds top pkg (os)", "os" in prov)
    check("provided: from-import names", {"scrub", "_xlate"} <= prov)

def t_move_reimport_zero_diff():
    before = _project(_BEFORE)
    after = _project(_AFTER_MOVE)
    check("move: scrub still defined-locally before", "scrub" in set(before["provided"]))
    check("move: scrub still provided after (via import)", "scrub" in set(after["provided"]))
    diffs = s.diff_surface(after, before)
    check("move+reimport is ZERO-diff", diffs == [], " | ".join(diffs))

def t_real_drop():
    before = _project(_BEFORE)
    golden = {
        "routes": before["routes"] + ["DELETE /v1/old -> old_handler"],
        "provided": before["provided"] + ["removed_symbol"],
    }
    diffs = s.diff_surface(before, golden)
    blob = " | ".join(diffs)
    check("drop: REMOVED route flagged", "REMOVED 'DELETE /v1/old -> old_handler'" in blob, blob)
    check("drop: REMOVED symbol flagged", "REMOVED 'removed_symbol'" in blob, blob)
    check("identical == clean", s.diff_surface(before, before) == [])

_ROUTER_FIXTURE = '''\
from fastapi import APIRouter

r = APIRouter(prefix="/v1/x")
empty = APIRouter(prefix="")
orphan = APIRouter(prefix="/orphan")

@r.get("/y")
def handler_y():
    return 1

@r.post("/z")
async def handler_z(req):
    return req

@empty.get("/e")
def handler_e():
    return 2

@orphan.get("/o")
def handler_o():
    return 3

@app.get("/plain")
def plain_handler():
    return 4

app.include_router(r, prefix="/api")
app.include_router(empty)
'''

def t_router_routes():
    routes = set(_project(_ROUTER_FIXTURE)["routes"])
    check("router: composed GET /api/v1/x/y", "GET /api/v1/x/y -> handler_y" in routes, routes)
    check("router: composed POST (async handler)", "POST /api/v1/x/z -> handler_z" in routes, routes)
    check("router: empty prefixes -> /e", "GET /e -> handler_e" in routes, routes)
    check("router: un-mounted uses router prefix only", "GET /orphan/o -> handler_o" in routes, routes)
    check("router: @app route unchanged alongside routers", "GET /plain -> plain_handler" in routes, routes)
    check("router: no mis-ordered path", not any("/v1/x/api" in rt for rt in routes), routes)

def t_router_method_set_matches_app():
    methods = list(s._ROUTE_METHODS)
    lines = ["r = APIRouter()"]
    for i, m in enumerate(methods):
        lines += [f'@app.{m}("/a{i}")', f"def a{i}(): pass",
                  f'@r.{m}("/b{i}")', f"def b{i}(): pass"]
    lines.append("app.include_router(r)")
    routes = set(_project("\n".join(lines) + "\n")["routes"])
    app_methods = {rt.split(" ", 1)[0] for rt in routes if " -> a" in rt}
    router_methods = {rt.split(" ", 1)[0] for rt in routes if " -> b" in rt}
    check("router method set == app method set", app_methods == router_methods,
          f"app={sorted(app_methods)} router={sorted(router_methods)}")
    check("router covers the full recognised method set", len(router_methods) == len(methods),
          f"{len(router_methods)} of {len(methods)}")

def t_app_to_router_zero_diff():
    as_app = _project('@app.get("/api/v1/x/y")\ndef h():\n    return 1\n')
    as_router = _project(
        'r = APIRouter(prefix="/v1/x")\n'
        '@r.get("/y")\n'
        'def h():\n    return 1\n'
        'app.include_router(r, prefix="/api")\n'
    )
    rec = "GET /api/v1/x/y -> h"
    check("zero-diff: @app form yields the record", rec in set(as_app["routes"]), as_app["routes"])
    check("zero-diff: router form yields the SAME record", rec in set(as_router["routes"]), as_router["routes"])
    check("zero-diff: route surface identical across the move",
          set(as_app["routes"]) == set(as_router["routes"]),
          f"{as_app['routes']} vs {as_router['routes']}")

def t_router_dynamic_prefix():
    routes = set(_project(
        'PFX = make_prefix()\n'
        'r = APIRouter(prefix=PFX)\n'
        '@r.get("/y")\n'
        'def h():\n    return 1\n'
        'app.include_router(r)\n'
    )["routes"])
    check("dynamic prefix collapses path to <dynamic>", "GET <dynamic> -> h" in routes, routes)

_PKG_MOD_X = '''\
from fastapi import APIRouter

router_x = APIRouter(prefix="/v1/x")

@router_x.get("/y")
def handler_y():
    return 1

@router_x.post("/z")
async def handler_z(req):
    return req
'''

def t_package_cross_file_include():
    files = {
        "mios_routes_x.py": _PKG_MOD_X,
        "entry.py": (
            "from fastapi import FastAPI\n"
            "from mios_routes_x import router_x\n"
            "app = FastAPI()\n"
            '@app.get("/plain")\n'
            "def plain_handler():\n    return 0\n"
            'app.include_router(router_x, prefix="/api")\n'
        ),
    }
    proj = _project_package(files, "entry.py")
    routes = set(proj["routes"])
    check("package: cross-file GET composed /api/v1/x/y",
          "GET /api/v1/x/y -> handler_y" in routes, routes)
    check("package: cross-file POST (async) composed",
          "POST /api/v1/x/z -> handler_z" in routes, routes)
    check("package: entry @app route preserved",
          "GET /plain -> plain_handler" in routes, routes)
    prov = set(proj["provided"])
    check("package: provided is entry-only (sibling handler absent)",
          "handler_y" not in prov and "router_x" in prov, sorted(prov))

def t_package_app_to_sibling_zero_diff():
    as_app = _project('@app.get("/api/v1/x/y")\ndef h():\n    return 1\n')
    as_pkg = _project_package(
        {
            "modx.py": (
                "from fastapi import APIRouter\n"
                'h_router = APIRouter(prefix="/v1/x")\n'
                '@h_router.get("/y")\n'
                "def h():\n    return 1\n"
            ),
            "entry.py": (
                "from fastapi import FastAPI\n"
                "from modx import h_router\n"
                "app = FastAPI()\n"
                'app.include_router(h_router, prefix="/api")\n'
            ),
        },
        "entry.py",
    )
    rec = "GET /api/v1/x/y -> h"
    check("package zero-diff: @app form yields the record", rec in set(as_app["routes"]))
    check("package zero-diff: sibling-router form yields the SAME record",
          rec in set(as_pkg["routes"]), as_pkg["routes"])
    check("package zero-diff: route surface identical across the cross-file move",
          set(as_app["routes"]) == set(as_pkg["routes"]),
          f"{as_app['routes']} vs {as_pkg['routes']}")

def t_package_one_level_nesting():
    files = {
        "mios_nest.py": (
            "from fastapi import APIRouter\n"
            'child = APIRouter(prefix="/c")\n'
            '@child.get("/leaf")\n'
            "def leaf_handler():\n    return 1\n"
            'parent = APIRouter(prefix="/p")\n'
            'parent.include_router(child, prefix="/sub")\n'
            '@parent.get("/direct")\n'
            "def direct_handler():\n    return 2\n"
        ),
        "entry.py": (
            "from fastapi import FastAPI\n"
            "from mios_nest import parent\n"
            "app = FastAPI()\n"
            'app.include_router(parent, prefix="/api")\n'
        ),
    }
    routes = set(_project_package(files, "entry.py")["routes"])
    check("package nesting: parent direct route composed /api/p/direct",
          "GET /api/p/direct -> direct_handler" in routes, routes)
    check("package nesting: child composed /api/p/sub/c/leaf",
          "GET /api/p/sub/c/leaf -> leaf_handler" in routes, routes)

def t_package_unresolved_degrades():
    files = {
        "mios_routes_x.py": _PKG_MOD_X,
        "entry.py": (
            "from fastapi import FastAPI\n"
            "from mios_routes_x import router_x\n"
            "from mios_missing import ghost_router\n"
            "app = FastAPI()\n"
            'app.include_router(router_x, prefix="/api")\n'
            'app.include_router(ghost_router, prefix="/nope")\n'
        ),
    }
    proj = _project_package(files, "entry.py")
    routes = set(proj["routes"])
    check("package unresolved: resolvable router still composed",
          "GET /api/v1/x/y -> handler_y" in routes, routes)
    check("package unresolved: nothing fabricated for the missing module",
          not any("nope" in r or "ghost" in r for r in routes), routes)
    check("package unresolved: deterministic across repeated calls",
          _project_package(files, "entry.py") == proj)

def t_package_dynamic_mount_degrades():
    files = {
        "mios_routes_x.py": _PKG_MOD_X,
        "entry.py": (
            "from fastapi import FastAPI\n"
            "from mios_routes_x import router_x\n"
            "app = FastAPI()\n"
            "PFX = make_prefix()\n"
            "app.include_router(router_x, prefix=PFX)\n"
        ),
    }
    routes = set(_project_package(files, "entry.py")["routes"])
    check("package dynamic mount: path collapses, handler preserved",
          "GET <dynamic> -> handler_y" in routes, routes)

def t_package_superset_of_surface_on_current_tree():
    server = os.path.join(os.path.dirname(os.path.abspath(__file__)), "server.py")
    if not os.path.isfile(server):
        check("package>=surface (server.py absent -> skipped)", True)
        return
    surf = s.project_surface(server)
    pkg = s.project_package(server)
    surf_routes, pkg_routes = set(surf["routes"]), set(pkg["routes"])
    check("package routes are a superset of surface routes", surf_routes <= pkg_routes,
          f"surface routes package lacks: {sorted(surf_routes - pkg_routes)}")
    check("package routes count >= surface routes count",
          pkg["counts"]["routes"] >= surf["counts"]["routes"],
          f"{pkg['counts']} vs {surf['counts']}")
    check("package preserves the provided surface (entry-only, not aggregated)",
          pkg["provided"] == surf["provided"],
          f"pkg-only={sorted(set(pkg['provided']) - set(surf['provided']))} "
          f"surf-only={sorted(set(surf['provided']) - set(pkg['provided']))}")
    check("package preserves the provided count",
          pkg["counts"]["provided"] == surf["counts"]["provided"], str(pkg["counts"]))

def _cli(*args):
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        rc = s.main(["mios_surface", *map(str, args)])
    return rc, stdout.getvalue(), stderr.getvalue()


def t_cli_atomic_output():
    with tempfile.TemporaryDirectory(prefix="mios-surface-output-") as tmp:
        tmp = Path(tmp)
        entry, output = tmp / "entry.py", tmp / "surface.json"
        entry.write_text(_BEFORE, encoding="utf-8")
        rc, stdout, _ = _cli(entry)
        expected = s.project_surface(str(entry))
        check("CLI stdout retains the canonical projection and newline",
              rc == 0 and json.loads(stdout) == expected and stdout.endswith("\n"))
        rc, stdout, _ = _cli(entry, "--out", output)
        check("CLI --out writes the complete stdout projection without emitting JSON",
              rc == 0 and stdout == "" and json.loads(output.read_text()) == expected)
        check("new golden is readable", os.name != "posix" or stat.S_IMODE(output.stat().st_mode) == 0o644)
        output.chmod(0o640)
        before = output.stat()
        rc, _, _ = _cli(entry, "--out", output)
        check("identical projection preserves the output inode and timestamps",
              rc == 0 and output.stat().st_ino == before.st_ino
              and output.stat().st_mtime_ns == before.st_mtime_ns)
        check("CLI --check still reports an exact match", _cli(entry, "--check", output)[0] == 0)
        stale = dict(expected, routes=expected["routes"] + ["GET /stale -> stale"])
        output.write_text(json.dumps(stale), encoding="utf-8")
        before_bytes = output.read_bytes()
        rc, _, error = _cli(entry, "--check", output)
        check("check mode rejects drift and leaves the golden unchanged",
              rc == 1 and "REMOVED" in error and output.read_bytes() == before_bytes)
        rc, _, _ = _cli(entry, "--out", output)
        check("atomic replacement repairs drift and preserves existing permissions",
              rc == 0 and json.loads(output.read_text()) == expected
              and (os.name != "posix" or stat.S_IMODE(output.stat().st_mode) == 0o640))
        before_bytes = output.read_bytes()
        for args in [
            (entry, "--out"),
            (entry, "--out", output, "--unknown"),
            (entry, "--out", output, "--check", output),
            (entry, "--out", output, "--search-dir"),
            (entry, "--out", output, "--search-dir", tmp),
            (entry, "--out", output, "extra.py"),
            (),
        ]:
            rc, stdout, _ = _cli(*args)
            check(f"invalid CLI arguments fail before writes: {args[1:] if args else 'missing target'}",
                  rc == 2 and stdout == "" and output.read_bytes() == before_bytes)
        with mock.patch.object(s.os, "replace", side_effect=OSError("planted replace failure")):
            # Force changed bytes so the atomic replacement is actually attempted.
            entry.write_text(_BEFORE + "\nNEW_SYMBOL = 1\n", encoding="utf-8")
            rc, stdout, error = _cli(entry, "--out", output)
        check("replace failure preserves the previous golden and reports failure",
              rc == 1 and stdout == "" and "planted replace failure" in error
              and output.read_bytes() == before_bytes)
        check("failed replacement removes the private temporary file",
              not list(tmp.glob(".mios-surface-*")))
        with mock.patch.object(s.os, "fsync", side_effect=OSError("planted write failure")):
            rc, stdout, error = _cli(entry, "--out", output)
        check("incomplete durable write preserves the previous golden and cleans temporary output",
              rc == 1 and stdout == "" and "planted write failure" in error
              and output.read_bytes() == before_bytes and not list(tmp.glob(".mios-surface-*")))
        entry.write_text("def broken(:\n", encoding="utf-8")
        rc, _, _ = _cli(entry, "--out", output)
        check("invalid source cannot partially replace the golden",
              rc == 1 and output.read_bytes() == before_bytes)
        entry.write_text(_BEFORE, encoding="utf-8")
        rc, _, _ = _cli(entry, "--out", tmp)
        check("directory output fails closed", rc == 1 and output.read_bytes() == before_bytes)
        if os.name == "posix":
            link = tmp / "golden-link"
            link.symlink_to(output)
            rc, _, _ = _cli(entry, "--out", link)
            check("symlink output fails closed without following or replacing it",
                  rc == 1 and link.is_symlink() and output.read_bytes() == before_bytes)
        source_before = entry.read_bytes()
        check("output cannot replace the projection source",
              _cli(entry, "--out", entry)[0] == 1 and entry.read_bytes() == source_before)


def t_declared_generation_step():
    root = Path(__file__).resolve().parents[4]
    data = tomllib.loads((root / "usr/share/mios/mios.toml").read_text())
    steps = [step for step in data["generation"]["sync"]["steps"] if step["id"] == "ai.surface"]
    check("surface projection is declared exactly once in generation.sync", len(steps) == 1)
    if len(steps) != 1:
        return
    calls = steps[0]["calls"]
    expected = {
        "script": "usr/lib/mios/agent-pipe/mios_surface.py",
        "args": ["{root}/usr/lib/mios/agent-pipe/server.py", "--package", "--out",
                 "{root}/usr/share/mios/ai/v1/surface.generated.json"],
    }
    check("declared surface stage uses the canonical whole-package projector", calls == [expected])
    generator = os.environ.get("MIOS_GEN") or shutil.which("mios-gen")
    check("native generation runner exists", bool(generator))
    if not generator:
        return
    with tempfile.TemporaryDirectory(prefix="mios-surface-stage-") as tmp:
        tmp = Path(tmp)
        script = tmp / expected["script"]
        script.parent.mkdir(parents=True)
        shutil.copyfile(s.__file__, script)
        entry = script.with_name("server.py")
        entry.write_text("from mios_fixture_routes import router\napp.include_router(router)\n", encoding="utf-8")
        script.with_name("mios_fixture_routes.py").write_text(
            'from fastapi import APIRouter\nrouter = APIRouter(prefix="/v1")\n'
            '@router.get("")\ndef mounted_handler():\n    return {}\n', encoding="utf-8")
        golden = tmp / "usr/share/mios/ai/v1/surface.generated.json"
        golden.parent.mkdir(parents=True)
        toml = tmp / "usr/share/mios/mios.toml"
        toml.write_text('[generation.sync]\nunit_projections=[]\n[[generation.sync.steps]]\n'
                        'id="ai.surface"\ncalls=[{script=' + json.dumps(expected["script"])
                        + ', args=' + json.dumps(expected["args"]) + '}]\n')
        subprocess.run(["git", "init", "-q", str(tmp)], check=True, capture_output=True)
        run = subprocess.run([generator, "sync", "--root", str(tmp)], capture_output=True, text=True, timeout=60)
        check("native sync runs the declared projector into its canonical output",
              run.returncode == 0 and golden.is_file(), run.stdout + run.stderr)
        if run.returncode == 0 and golden.is_file():
            generated = json.loads(golden.read_text())
            check("declared stage includes real mounted package routes",
                  generated == s.project_package(str(entry))
                  and "GET /v1 -> mounted_handler" in generated["routes"])
            original = golden.read_bytes()
            entry.write_text("def broken(:\n")
            failed = subprocess.run([generator, "sync", "--root", str(tmp)],
                                    capture_output=True, text=True, timeout=60)
            check("native stage propagates projector failure without truncating output",
                  failed.returncode != 0 and golden.read_bytes() == original
                  and "ai.surface" in failed.stderr)


def t_real_app_projection_parity():
    # Read the actual application table without starting FastAPI lifespan, daemons,
    # sockets or inference. Reuse the runtime parity gate's route normalization.
    import fastapi
    from starlette.routing import WebSocketRoute
    from test_mios_routing import _app_route_pairs, _golden_route_pairs, _resolve_mios_toml
    _resolve_mios_toml()
    import server
    check("surface runtime proof uses the real FastAPI application",
          isinstance(server.app, fastapi.FastAPI) and bool(server.app.routes))
    projected = s.project_package(server.__file__)
    actual = _app_route_pairs(server.app, WebSocketRoute)
    check("whole-package projection equals the real application method/path table",
          _golden_route_pairs(projected) == actual,
          f"projected-only={sorted(_golden_route_pairs(projected) - actual)} "
          f"runtime-only={sorted(actual - _golden_route_pairs(projected))}")
    check("whole-package route count matches the nonempty runtime surface",
          projected["counts"]["routes"] == len(actual) and len(actual) > 0)
    golden = Path(__file__).resolve().parents[4] / "usr/share/mios/ai/v1/surface.generated.json"
    check("committed golden is the exact canonical package projection",
          json.loads(golden.read_text()) == projected)
    # Regress the concrete missing mounted route and prove the same parity contract detects it.
    without = dict(projected, routes=[r for r in projected["routes"] if not r.startswith("GET /v1 -> ")])
    check("mounted /v1 route omission is detected by runtime parity",
          ("GET", "/v1") in actual and _golden_route_pairs(without) != actual
          and bool(s.diff_surface(projected, without)))
    extra = dict(projected, routes=projected["routes"] + ["GET /mios-fixture-absent -> absent"])
    check("fabricated extra route is detected by runtime parity",
          _golden_route_pairs(extra) != actual and bool(s.diff_surface(projected, extra)))


def main():
    t_routes()
    t_provided()
    t_move_reimport_zero_diff()
    t_real_drop()
    t_router_routes()
    t_router_method_set_matches_app()
    t_app_to_router_zero_diff()
    t_router_dynamic_prefix()
    t_package_cross_file_include()
    t_package_app_to_sibling_zero_diff()
    t_package_one_level_nesting()
    t_package_unresolved_degrades()
    t_package_dynamic_mount_degrades()
    t_package_superset_of_surface_on_current_tree()
    t_cli_atomic_output()
    t_declared_generation_step()
    t_real_app_projection_parity()
    print(f"\n{'ok' if _fails == 0 else str(_fails) + ' FAILED'}")
    return 1 if _fails else 0



# ==============================================================================
# Consolidated from test_mios_env.py (task_1006)
# ==============================================================================
# AI-hint: Unit test for empty MIOS_* env contract.

import os
import sys
import unittest

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
USR_LIB = os.path.abspath(os.path.join(BASE_DIR, ".."))
if USR_LIB not in sys.path:
    sys.path.insert(0, USR_LIB)
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from mios_env import strip_empty_mios_env

class TestMiosEnvContract(unittest.TestCase):
    def test_strip_empty_mios_env(self):
        test_env = {
            "MIOS_FIXTURE_FOO": "",
            "MIOS_FIXTURE_BAR": "123",
            "OTHER_VAR": "",
        }
        stripped = strip_empty_mios_env(test_env)
        self.assertNotIn("MIOS_FIXTURE_FOO", stripped)
        self.assertIn("MIOS_FIXTURE_BAR", stripped)
        self.assertIn("OTHER_VAR", stripped)

    def test_import_agent_pipe_with_empty_env(self):
        sample_keys = [
            "MIOS_FIXTURE_PORT_AGENT_PIPE",
            "MIOS_FIXTURE_PORT_HTTP",
            "MIOS_FIXTURE_TIMEOUT",
            "MIOS_FIXTURE_MAX_WORKERS",
            "MIOS_FIXTURE_ENABLE_FEATURE",
        ]
        for k in sample_keys:
            os.environ[k] = ""

        import mios_pipe
        strip_empty_mios_env(os.environ)
        import mios_pipe.kernel.config

        for k in sample_keys:
            self.assertNotIn(k, os.environ)


def _run_extra_env():
    import os
    _saved_env = dict(os.environ)
    try:
        import unittest
        suite = unittest.TestSuite()
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestMiosEnvContract))
        res = unittest.TextTestRunner().run(suite)
        return 0 if res.wasSuccessful() else 1
    except SystemExit as _e:
        return _e.code if _e.code is not None else 0
    finally:
        os.environ.clear()
        os.environ.update(_saved_env)



# ==============================================================================
# Consolidated from test_mios_toolsurface.py (task_1006)
# ==============================================================================
# AI-hint: Unit tests for mios_pipe.routing.toolsurface.
"""Unit tests for worker tool surface assembly and child tool selection."""

import asyncio
import unittest

from mios_pipe.routing.toolsurface import (
    _select_child_tools,
    _worker_tools_surface,
    configure as configure_toolsurface,
)

def mock_verb_to_openai_tool(name, cfg):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": cfg.get("description", ""),
            "parameters": {"type": "object", "properties": {}},
        },
    }

class TestToolSurface(unittest.TestCase):

    def setUp(self):
        self.stub_catalog = {
            "read_file": {"permission": "read", "description": "Read file contents"},
            "run_cmd": {"permission": "exec", "description": "Run terminal command"},
        }
        configure_toolsurface(
            worker_tools_scope="all",
            child_tool_select=True,
            stable_prefix=False,
            child_tool_floor=1,
            verb_catalog=self.stub_catalog,
            verb_to_openai_tool=mock_verb_to_openai_tool,
        )

    def test_worker_tools_surface_shape(self):
        surface = _worker_tools_surface()
        self.assertEqual(len(surface), 2)
        names = [t["function"]["name"] for t in surface]
        self.assertIn("read_file", names)
        self.assertIn("run_cmd", names)

    def test_select_child_tools_capping(self):
        surface = _worker_tools_surface()
        res = asyncio.run(_select_child_tools(surface, "read a file", cap=1))
        self.assertEqual(len(res), 1)


def _run_extra_toolsurface():
    import os
    _saved_env = dict(os.environ)
    try:
        import unittest
        suite = unittest.TestSuite()
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestToolSurface))
        res = unittest.TextTestRunner().run(suite)
        return 0 if res.wasSuccessful() else 1
    except SystemExit as _e:
        return _e.code if _e.code is not None else 0
    finally:
        os.environ.clear()
        os.environ.update(_saved_env)



def _run_all_folded_surface_suites():
    rc = _run_extra_env()
    if rc not in (None, 0):
        import sys
        sys.exit(f"Folded test suite failed: exit code {rc}")
    rc = _run_extra_toolsurface()
    if rc not in (None, 0):
        import sys
        sys.exit(f"Folded test suite failed: exit code {rc}")

if __name__ == "__main__":
    _rc_main = main()
    _run_all_folded_surface_suites()
    sys.exit(_rc_main)
