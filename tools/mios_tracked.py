#!/usr/bin/env python3
# AI-hint: Lists tracked files for a gate, raising when git could not answer -- an empty listing is never reported as a clean corpus.
# AI-related: tools/check-testhygiene.py, tools/check-docs.py, tools/check-ssot.py, tools/sync-bootstrap.py
# AI-functions: tracked, tracked_modes, native_install_paths, rooted_target, rooted_resolve
"""One way to ask git what is tracked, so a refusal cannot read as "nothing".

Raises on a non-zero exit AND on an empty listing. See f66e6efc.
"""
from __future__ import annotations

import os
import subprocess
import json
import re
import stat
from pathlib import Path


class GitUnavailable(RuntimeError):
    """git could not enumerate the tree, so no scan of it means anything."""


def tracked(root: str, *pathspec: str) -> list:
    """Tracked paths under root, slash-separated. Raises GitUnavailable."""
    cmd = ["git", "-C", root, "ls-files", *pathspec]
    p = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if p.returncode != 0:
        raise GitUnavailable(
            "git ls-files failed in %s (exit %d): %s"
            % (root, p.returncode, (p.stderr or "").strip() or "no message"))
    paths = [l.strip().replace(os.sep, "/") for l in p.stdout.splitlines() if l.strip()]
    if not paths:
        raise GitUnavailable(
            "git ls-files listed no tracked file in %s%s, so nothing would be "
            "scanned and the result would be clean for the wrong reason"
            % (root, (" for " + " ".join(pathspec)) if pathspec else ""))
    return paths


class NativeCatalogError(RuntimeError):
    """The native installation contract could not be verified."""


def native_install_paths(root: str) -> dict[str, str]:
    """Exact Linux destinations from the native SSOT/Cargo ownership planner."""
    import tomllib
    try:
        policy = rooted_resolve(root, "usr/share/mios/mios.toml")
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise NativeCatalogError(f"cannot resolve native installation policy: {exc}") from exc
    try:
        with policy.open("rb") as fh:
            data = tomllib.load(fh)
        native = data.get("build", {}).get("native")
    except (OSError, ValueError, AttributeError) as exc:
        raise NativeCatalogError(f"cannot read native installation policy: {exc}") from exc
    if native is None:
        return {}
    if not isinstance(native, dict) or not isinstance(native.get("categories"), dict):
        raise NativeCatalogError("native installation categories are missing or malformed")

    catalog = os.environ.get("MIOS_NATIVE_BIN_DIR")
    if catalog:
        candidates = [Path(catalog) / ("miosd.exe" if os.name == "nt" else "miosd")]
    else:
        candidates = [Path(root) / "usr/bin/miosd", Path(root) / "usr/libexec/mios/miosd",
                      Path(root) / "src/mios-rs/target/debug/miosd",
                      Path("/usr/bin/miosd"), Path("/usr/libexec/mios/miosd")]
    executable = next((p for p in candidates if p.is_file() and os.access(p, os.X_OK)), None)
    if executable is None:
        raise NativeCatalogError("native miosd is required to verify installation metadata")
    try:
        result = subprocess.run([str(executable), "native-targets", "--root", str(root),
                                 "--platform", "linux", "--json"],
                                capture_output=True, text=True, check=False)
        if result.returncode:
            raise NativeCatalogError("native installation metadata failed: " +
                                     (result.stderr.strip() or result.stdout.strip()))
        rows = json.loads(result.stdout)
    except (OSError, ValueError) as exc:
        raise NativeCatalogError(f"cannot read native installation metadata: {exc}") from exc
    if not isinstance(rows, list) or not rows:
        raise NativeCatalogError("native installation metadata selected no executables")
    workspaces = native.get("workspaces")
    if (not isinstance(workspaces, list) or not workspaces
            or any(not isinstance(w, str) for w in workspaces)):
        raise NativeCatalogError("malformed native workspaces")
    expected = set()
    for category in native["categories"].values():
        if (not isinstance(category, dict) or not isinstance(category.get("binaries"), list)
                or any(not isinstance(n, str) for n in category["binaries"])
                or category.get("install_dir") not in ("/usr/bin", "/usr/libexec/mios")
                or not isinstance(category.get("compat_dirs", []), list)
                or any(d not in ("/usr/bin", "/usr/libexec/mios") for d in category.get("compat_dirs", []))
                or type(category.get("expose_bin", False)) is not bool):
            raise NativeCatalogError("malformed native category")
        if len(set(category["binaries"])) != len(category["binaries"]) or expected.intersection(category["binaries"]):
            raise NativeCatalogError("duplicate native executable declaration")
        expected.update(category["binaries"])
    excluded = native.get("windows_only", [])
    if (not isinstance(excluded, list) or any(not isinstance(n, str) for n in excluded)
            or not set(excluded).issubset(expected)):
        raise NativeCatalogError("malformed Windows-only native declaration")
    expected.difference_update(excluded)
    aliases = native.get("aliases", {})
    if not isinstance(aliases, dict):
        raise NativeCatalogError("malformed native aliases")
    for name, route in aliases.items():
        if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", name)
                or name in expected or not isinstance(route, dict)
                or set(route) != {"binary", "command", "platform"}
                or route.get("binary") not in expected or route.get("platform") != "linux"
                or not isinstance(route.get("command"), str) or not route["command"]):
            raise NativeCatalogError("malformed native alias declaration")
    observed, paths = set(), {}
    for row in rows:
        if not isinstance(row, dict):
            raise NativeCatalogError("malformed native installation row")
        binary, category = row.get("binary"), row.get("category")
        if not isinstance(category, str):
            raise NativeCatalogError("malformed native category identifier")
        group = native["categories"].get(category)
        if (not isinstance(binary, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", binary)
                or binary in observed or not isinstance(group, dict)
                or binary not in group.get("binaries", [])
                or row.get("platform") != "linux"
                or row.get("workspace") not in native.get("workspaces", [])
                or not isinstance(row.get("package"), str) or not row["package"]
                or row.get("install_dir") != group.get("install_dir")
                or row.get("compat_dirs") != group.get("compat_dirs", [])
                or row.get("expose_bin") is not group.get("expose_bin", False)):
            raise NativeCatalogError(f"inconsistent native installation metadata for {binary!r}")
        declared_aliases = sorted(name for name, route in aliases.items() if route["binary"] == binary)
        if row.get("named_aliases", []) != declared_aliases:
            raise NativeCatalogError(f"inconsistent native aliases for {binary}")
        directories = [row["install_dir"], *row["compat_dirs"]]
        if row["expose_bin"]:
            directories.append("/usr/bin")
        if any(d not in {"/usr/bin", "/usr/libexec/mios"} for d in directories):
            raise NativeCatalogError(f"unsupported native installation path for {binary}")
        observed.add(binary)
        for directory in directories:
            for name in [binary, *declared_aliases]:
                paths[directory + "/" + name] = binary
    if observed != expected:
        raise NativeCatalogError("native installation metadata does not cover the declared Linux catalog")
    return paths


def _root_input(root: str, path: str):
    root_name = Path(os.path.abspath(root)).as_posix().rstrip("/") or "/"
    text = os.fspath(path)
    if os.name == "nt":
        text = text.replace("\\", "/")
    if any(ord(c) < 32 for c in text):
        raise ValueError("control character in rooted path")
    if root_name != "/" and (text == root_name or text.startswith(root_name + "/")):
        text = text[len(root_name):]
    parts = [p for p in text.split("/") if p not in ("", ".")]
    if os.name == "nt" and re.match(r"^[A-Za-z]:", text):
        raise ValueError("drive-qualified target escapes the checkout root")
    return text.startswith("/"), parts


def _root_parts(root: str, path: str, parent=()) -> list[str]:
    """Lexical image-relative components; reject traversal above the image root."""
    absolute, incoming = _root_input(root, path)
    parts = [] if absolute else list(parent)
    for part in incoming:
        if part == "..":
            if not parts:
                raise ValueError("symlink target escapes the checkout root")
            parts.pop()
        else:
            parts.append(part)
    return parts


def rooted_target(root: str, link: str, target: str) -> Path:
    """Map a link target into the image root without following host paths."""
    link_parts = _root_parts(root, link)
    return Path(os.path.abspath(root)).joinpath(*_root_parts(root, target, link_parts[:-1]))


def rooted_resolve(root: str, path: str, declared_paths=()) -> Path:
    """Resolve each link component inside root, allowing only exact declared outputs."""
    root_path = Path(os.path.abspath(root))
    _, pending = _root_input(root, path)
    parts, followed = [], 0
    declarations = set(declared_paths)
    while pending:
        part = pending.pop(0)
        if part == "..":
            if not parts:
                raise ValueError("symlink target escapes the checkout root")
            parts.pop()
            continue
        candidate = root_path.joinpath(*parts, part)
        try:
            mode = candidate.lstat().st_mode
        except FileNotFoundError:
            tail = [*parts, part, *pending]
            if ".." not in pending and "/" + "/".join(tail) in declarations:
                return root_path.joinpath(*tail)
            raise
        if stat.S_ISLNK(mode):
            followed += 1
            if followed > 40:
                raise ValueError("symlink cycle or excessive indirection")
            target = os.readlink(candidate)
            if not target:
                raise ValueError("empty symlink target")
            absolute, target_parts = _root_input(root, target)
            pending = target_parts + pending
            if absolute:
                parts = []
        else:
            if pending and not stat.S_ISDIR(mode):
                raise NotADirectoryError(str(candidate))
            parts.append(part)
    return root_path.joinpath(*parts)


def tracked_modes(root: str) -> dict[str, str]:
    """Read index node types without Git's human path quoting or host dereference."""
    if not os.path.lexists(os.path.join(root, ".git")):
        raise GitUnavailable("tracked node root is not a Git checkout")
    env = {key: value for key, value in os.environ.items()
           if key not in {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"}}
    result = subprocess.run(["git", "-C", root, "ls-files", "--stage", "-z"],
                            capture_output=True, check=False, env=env)
    if result.returncode:
        raise GitUnavailable("git cannot enumerate tracked node modes: " +
                             result.stderr.decode("utf-8", "replace").strip())
    modes = {}
    try:
        for record in result.stdout.decode("utf-8").split("\0"):
            if not record:
                continue
            meta, path = record.split("\t", 1)
            mode, _, stage = meta.split()
            if stage != "0" or mode not in {"100644", "100755", "120000", "160000"}:
                raise ValueError("unmerged or unsupported tracked node")
            modes[path] = mode
    except ValueError as exc:
        raise GitUnavailable(f"invalid tracked node metadata: {exc}") from exc
    if not modes:
        raise GitUnavailable("git listed no tracked nodes")
    return modes
