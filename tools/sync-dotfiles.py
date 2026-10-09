#!/usr/bin/env python3
# AI-hint: Syncs the .dotfiles SSOT to IDE profiles and skel; merges its client-portable subset (ADR-0024) into each devcontainer.json / *.code-workspace and projects [dotfiles.devcontainer] and [workspace] keys into them.
# AI-doc: usr/share/doc/mios/manual/tools.md
import argparse
import json
import glob
import os
import re
import shutil
import stat
import sys
import tempfile

_HERE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
# Data root (MIOS_ROOT, as every other generator sync-generated.sh drives); the
# resolver library always comes from the checkout this tool ships in.
REPO_ROOT = os.path.abspath(os.environ.get("MIOS_ROOT") or _HERE_ROOT)
DOTFILES_DIR = os.path.join(REPO_ROOT, ".dotfiles")
BOOTSTRAP_ROOT = os.path.abspath(
    os.environ.get("MIOS_BOOTSTRAP_ROOT") or os.path.join(REPO_ROOT, "..", "mios-bootstrap"))
VENDOR_TOML = os.path.join(REPO_ROOT, "usr/share/mios/mios.toml")

sys.path.insert(0, os.path.join(_HERE_ROOT, "usr/lib/mios"))
import mios_toml  # noqa: E402

VSCODE_SETTINGS_SRC = os.path.join(DOTFILES_DIR, "vscode", "settings.json")
CODESERVER_SETTINGS_SRC = os.path.join(DOTFILES_DIR, "code-server", "settings.json")
# The code-server stylesheet is RENDERED (mios-dotfiles-render surface code-server-terminal) into
# usr/share/mios/themes/; these former byte copies of it must not come back.
STALE_CSS_COPIES = (".dotfiles/code-server/code-server-terminal.css",
                    "usr/share/mios/dotfiles/code-server/code-server-terminal.css")

EXIT_DRIFT = 1
EXIT_MISSING_SOURCE = 2
EXIT_BAD_POLICY = 3

# Byte copies: settings FILES a client reads, so they keep the FULL profile
# (an unknown key in a file only warns; ADR-0024).
TARGET_PROJECTIONS = [
    # (source_path, target_rel_path)
    (VSCODE_SETTINGS_SRC, "etc/skel/.vscode/settings.json"),
    (VSCODE_SETTINGS_SRC, "etc/skel/.config/Code/User/settings.json"),
    (VSCODE_SETTINGS_SRC, ".vscode/settings.json"),
    (CODESERVER_SETTINGS_SRC, "etc/skel/.local/share/code-server/User/settings.json"),
    (CODESERVER_SETTINGS_SRC, "usr/share/mios/agents/code-server-mobile-settings.json"),
]

# The [dotfiles.vscode] lists, and the ones without which the partition means nothing.
_POLICY_LISTS = ("desktop_only_keys", "user_only_keys", "unregistered_keys",
                 "client_portable_surfaces", "bootstrap_client_portable_surfaces")
_POLICY_REQUIRED = ("desktop_only_keys", "unregistered_keys", "client_portable_surfaces")


def _fatal(msg, code):
    print(f"[sync-dotfiles] FATAL: {msg}", file=sys.stderr)
    return code


def _vendor_merged():
    if not os.path.isfile(VENDOR_TOML):
        raise SystemExit(_fatal(f"vendor SSOT missing: {VENDOR_TOML}", EXIT_MISSING_SOURCE))
    frag_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(VENDOR_TOML)))),
                            "usr", "lib", "mios", "mios.d")
    frags = sorted(glob.glob(os.path.join(frag_dir, "*.toml")))  # Law 13: vendor fragments, as the gate reads them
    return mios_toml.load_merged(layers=[VENDOR_TOML, *frags])


def _name_list(dc, key, what):
    names = dc.get(key)
    if not isinstance(names, list) or not names or not all(isinstance(k, str) and k for k in names):
        raise SystemExit(_fatal(f"mios.toml [dotfiles.devcontainer].{key} must be a non-empty list of {what}",
                                EXIT_BAD_POLICY))
    return names


def devcontainer_projection():
    """What every devcontainer.json owns, as the resolver emits it (emit_exports: stack_id offset and
    ${MIOS_*} applied): forwardPorts from each [ports] key of [dotfiles.devcontainer].forward_port_keys,
    in order; containerEnv[n] for each MIOS_* name n of .container_env_keys; remoteUser from
    [identity].username; hostRequirements from .host_requirements. Exit 3 on an absent or empty list,
    a name without a resolved (integer, for a port) value, or an invalid user or host requirement."""
    merged = _vendor_merged()
    dc = mios_toml.section(merged, "dotfiles.devcontainer")
    keys = _name_list(dc, "forward_port_keys", "[ports] key names")
    env = {n: None for n in _name_list(dc, "container_env_keys", "emitted MIOS_* names")}
    exports = mios_toml.emit_exports(merged)
    ports = [exports.get(f"MIOS_PORTS_{k.upper()}", "") for k in keys]
    for k, v in zip(keys, ports):
        if not v.isdigit():
            raise SystemExit(_fatal(f"mios.toml [dotfiles.devcontainer].forward_port_keys names {k!r}, "
                                    "which is not an integer [ports] key", EXIT_BAD_POLICY))
    for n in env:
        env[n] = exports.get(n)
        if not env[n] or "${" in env[n]:
            raise SystemExit(_fatal(f"mios.toml [dotfiles.devcontainer].container_env_keys names {n!r}, which "
                                    f"the resolver does not emit as a resolved value ({env[n]!r})", EXIT_BAD_POLICY))
    ws = mios_toml.section(merged, "workspace")
    repos, primary = ws.get("repos"), ws.get("primary")
    if (not isinstance(repos, list) or not repos
            or not all(isinstance(r, dict) and r.get("name") and r.get("url") for r in repos)
            or primary not in [r["name"] for r in repos]
            or not str(ws.get("root") or "").startswith("/") or not ws.get("devcontainer")):
        raise SystemExit(_fatal("mios.toml [workspace] needs an absolute root, a devcontainer path and a "
                                "non-empty repos list of {name, url} that names primary", EXIT_BAD_POLICY))
    folders = [{"name": r.get("label") or r["name"], "path": "." if r["name"] == primary else f"../{r['name']}"}
               for r in repos]
    # The devcontainer runs [image].ref itself, so its user is the image's own
    # [identity].username, and its host must hold that image (.host_requirements).
    user = mios_toml.section(merged, "identity").get("username")
    if not isinstance(user, str) or not re.fullmatch(r"[a-z_][a-z0-9_-]*", user):
        raise SystemExit(_fatal(f"mios.toml [identity].username {user!r} is not a user name the devcontainer "
                                "can run as", EXIT_BAD_POLICY))
    host = dc.get("host_requirements")
    if (not isinstance(host, dict) or set(host) != {"cpus", "memory", "storage"}
            or not isinstance(host["cpus"], int) or isinstance(host["cpus"], bool) or host["cpus"] < 1
            or not all(isinstance(host[k], str) and re.fullmatch(r"[1-9][0-9]*gb", host[k])
                       for k in ("memory", "storage"))):
        raise SystemExit(_fatal("mios.toml [dotfiles.devcontainer].host_requirements must be "
                                "{ cpus = <int>, memory = \"<n>gb\", storage = \"<n>gb\" }", EXIT_BAD_POLICY))
    return {"forwardPorts": [int(v) for v in ports], "containerEnv": env,
            "remoteUser": user, "hostRequirements": dict(host),
            "workspaceFolder": ws["root"],
            # Pinned so a local clone under any directory name lands where Codespaces puts it.
            "workspaceMount": f"source=${{localWorkspaceFolder}},target={ws['root']}/{primary},type=bind",
            "workspaceDevcontainer": ws["devcontainer"], "folders": folders}


# [theme.edge] key -> the settings key it owns in both .dotfiles settings sources (operator decision: compact, ModernUI on).
EDGE_SETTINGS = {"code_server_density": "window.density.layout", "code_server_modern_ui": "workbench.experimental.modernUI"}


def project_edge_settings(check):
    """Render EDGE_SETTINGS from mios.toml [theme.edge] into each .dotfiles settings source in place; exit 3 on an absent key."""
    edge = mios_toml.section(_vendor_merged(), "theme.edge")
    missing = [k for k in EDGE_SETTINGS if k not in edge]
    if missing:
        raise SystemExit(_fatal(f"mios.toml [theme.edge] lacks {', '.join(missing)}, so the settings they own "
                                "cannot be projected", EXIT_BAD_POLICY))
    drift = []
    for src in (VSCODE_SETTINGS_SRC, CODESERVER_SETTINGS_SRC):
        label = os.path.relpath(src, REPO_ROOT)
        with open(src, encoding="utf-8", newline="") as fh:
            text = fh.read()
        doc, new = json.loads(text), text
        for tkey, skey in EDGE_SETTINGS.items():
            want = edge[tkey]
            if skey in doc and doc[skey] == want and type(doc[skey]) is type(want):
                continue
            drift.append((label, f"{skey} is {doc.get(skey, '<absent>')!r}, mios.toml [theme.edge].{tkey} "
                                 f"renders {want!r}"))
            pat = re.compile(r'^([ \t]*' + re.escape(json.dumps(skey)) + r'[ \t]*:[ \t]*)[^,\n]*?([ \t]*,?[ \t]*)$', re.M)
            if pat.search(new):
                new = pat.sub(lambda m: m.group(1) + json.dumps(want) + m.group(2), new, count=1)
            else:
                new = new.replace("{\n", "{\n  " + json.dumps(skey) + ": " + json.dumps(want) + ",\n", 1)
        if new != text and not check:
            if json.loads(new) != dict(doc, **{s: edge[t] for t, s in EDGE_SETTINGS.items()}):
                raise SystemExit(_fatal(f"{label}: the in-place edit of the [theme.edge] keys did not parse back", EXIT_BAD_POLICY))
            _write_atomic(src, new)
    return drift


def load_policy():
    """mios.toml [dotfiles.vscode] from the vendor tier. Exit 3 on an absent or
    empty partition: no list must never read as "nothing to prune" (ADR-0024)."""
    merged = _vendor_merged()
    pol = mios_toml.section(merged, "dotfiles.vscode")
    for key in _POLICY_LISTS:
        val = pol.get(key, [])
        if not isinstance(val, list) or not all(isinstance(x, str) and x for x in val):
            raise SystemExit(_fatal(
                f"mios.toml [dotfiles.vscode].{key} must be a list of non-empty strings", EXIT_BAD_POLICY))
    for key in _POLICY_REQUIRED:
        if not pol.get(key):
            raise SystemExit(_fatal(
                f"mios.toml [dotfiles.vscode].{key} is empty or absent -- the client-portable "
                "partition (ADR-0024) cannot be projected without it", EXIT_BAD_POLICY))
    return pol


def pruned_keys(pol):
    """Every key that must not reach a client-portable surface, tagged with WHY
    (the [dotfiles.vscode] list that names it) so a drift line can say so."""
    out = {}
    for key in pol.get("unregistered_keys", []):
        out[key] = "unregistered"
    for key in pol.get("user_only_keys", []):
        out[key] = "User-settings-only (APPLICATION scope)"
    for key in pol.get("desktop_only_keys", []):
        out[key] = "desktop-only"
    return out


def surface_key_path(rel_target):
    """Where the VS Code settings object lives in a surface, decided by the file
    type, not by a per-file literal: customizations.vscode.settings in a
    devcontainer.json, the top-level settings block in a *.code-workspace."""
    if rel_target.endswith(".code-workspace"):
        return ("settings",)
    if os.path.basename(rel_target) == "devcontainer.json":
        return ("customizations", "vscode", "settings")
    raise SystemExit(_fatal(
        f"[dotfiles.vscode] names a surface of unknown type: {rel_target} "
        "(only devcontainer.json and *.code-workspace carry an API-applied settings block)",
        EXIT_BAD_POLICY))


def client_surfaces(pol):
    """[(repo_root, rel_target, key_path, label)] for every client-portable surface
    the SSOT declares: this repository's, then mios-bootstrap's (Law 15)."""
    out = []
    for rel in pol.get("client_portable_surfaces", []):
        out.append((REPO_ROOT, rel, surface_key_path(rel)))
    for rel in pol.get("bootstrap_client_portable_surfaces", []):
        out.append((BOOTSTRAP_ROOT, rel, surface_key_path(rel)))
    return [(root, rel, kp, f"{os.path.basename(os.path.normpath(root))}/{rel}")
            for root, rel, kp in out]


def _get_in(d, path):
    for key in path:
        d = d.setdefault(key, {})
    return d


def _surface_mode(path):
    """The mode a rewritten surface keeps. An existing target keeps its own (a
    devcontainer.json tracked 100755 must not come back 100644); a new one gets
    what a plain open() would give it, 0o666 masked by the process umask."""
    try:
        return stat.S_IMODE(os.stat(path).st_mode)
    except FileNotFoundError:
        mask = os.umask(0)
        os.umask(mask)
        return 0o666 & ~mask


def _write_atomic(path, text):
    """Temp file beside the target + rename: a reader never sees a half-written
    surface, and a crash mid-write leaves the committed bytes untouched. The
    temp file takes the target's mode BEFORE the rename: mkstemp creates 0600
    and os.replace carries the temp file's mode, so without this every
    rewritten surface came back 0600 and lost its tracked bit."""
    d = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(d, exist_ok=True)
    mode = _surface_mode(path)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=f".{os.path.basename(path)}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _ssot_unregistered(pol, ssot_settings, label):
    """A key VS Code does not know must not sit in the SSOT at all: it would reach
    every byte copy (harmless but dead) and only the prune keeps it off the
    client surfaces. Refuse it at the source."""
    return [(label, f"unregistered key {k} is still in the SSOT -- delete it "
                    "(or drop it from [dotfiles.vscode].unregistered_keys if it came back upstream)")
            for k in pol.get("unregistered_keys", []) if k in ssot_settings]


def project_json_merges(check, pol, ssot_settings, dc_owned):
    """Merge the CLIENT-PORTABLE subset of the SSOT settings into every
    devcontainer.json / *.code-workspace settings block and PRUNE every
    [dotfiles.vscode] desktop-only / User-only / unregistered key already there.
    SSOT keys win on conflict; any surface-only key (installer-specific zenMode.*
    tuning) survives. A devcontainer.json's forwardPorts array, remoteUser,
    hostRequirements and the containerEnv entries in dc_owned are owned. Returns [(label, reason)] -- one line per key and file, so
    --check names exactly what is wrong where."""
    pruned = pruned_keys(pol)
    portable = {k: v for k, v in ssot_settings.items() if k not in pruned}
    drift = []
    for repo_root, rel_target, key_path, label in client_surfaces(pol):
        dst = os.path.join(repo_root, rel_target)
        if not os.path.isfile(dst):
            if repo_root == REPO_ROOT:
                drift.append((label, "client-portable surface named by [dotfiles.vscode] is missing"))
            # else: degrade open -- the sibling repo is not checked out here
            continue
        with open(dst, "r", encoding="utf-8") as f:
            doc = json.load(f)
        parent = _get_in(doc, key_path[:-1])
        existing = parent.get(key_path[-1], {})
        if not isinstance(existing, dict):
            drift.append((label, f"settings block at {'.'.join(key_path)} is not an object"))
            continue
        expected = {k: v for k, v in existing.items() if k not in pruned}
        expected.update(portable)
        reasons = [f"{pruned[k]} key {k} must not reach a client-portable surface (ADR-0024)"
                   for k in existing if k in pruned]
        for k, v in portable.items():
            if k not in existing:
                reasons.append(f"SSOT key {k} is missing from the surface")
            elif existing[k] != v:
                reasons.append(f"SSOT key {k} differs from the SSOT value")
        if expected != existing and not reasons:
            reasons.append("settings block differs from the SSOT projection")
        owned_stale = False
        if os.path.basename(rel_target) == "devcontainer.json":
            fwd, env = dc_owned["forwardPorts"], dc_owned["containerEnv"]
            if doc.get("forwardPorts") != fwd:
                reasons.append(f"forwardPorts {doc.get('forwardPorts')} differs from the "
                               f"[dotfiles.devcontainer].forward_port_keys projection {fwd}")
            cenv = doc.get("containerEnv") if isinstance(doc.get("containerEnv"), dict) else {}
            reasons.extend(f"containerEnv.{n} {cenv.get(n)!r} differs from the resolved SSOT value {v!r} "
                           "([dotfiles.devcontainer].container_env_keys)" for n, v in env.items() if cenv.get(n) != v)
            owned_stale = doc.get("forwardPorts") != fwd or any(cenv.get(n) != v for n, v in env.items())
            for key, source in (("remoteUser", "[identity].username"),
                                ("hostRequirements", "[dotfiles.devcontainer].host_requirements")):
                if doc.get(key) != dc_owned[key]:
                    reasons.append(f"{key} {doc.get(key)!r} differs from the {source} projection {dc_owned[key]!r}")
                    owned_stale = True
            if owned_stale:
                doc["forwardPorts"] = list(fwd)
                doc["containerEnv"] = dict(cenv, **env)
                doc["remoteUser"] = dc_owned["remoteUser"]
                doc["hostRequirements"] = dict(dc_owned["hostRequirements"])
        # [workspace]: the primary devcontainer opens the workspace root and every
        # *.code-workspace here lists the same repos, so each MiOS image opens one set.
        if repo_root == REPO_ROOT and rel_target == dc_owned["workspaceDevcontainer"]:
            for key in ("workspaceFolder", "workspaceMount"):
                if doc.get(key) != dc_owned[key]:
                    reasons.append(f"{key} {doc.get(key)!r} differs from the [workspace] projection {dc_owned[key]!r}")
                    doc[key] = dc_owned[key]
                    owned_stale = True
        if repo_root == REPO_ROOT and rel_target.endswith(".code-workspace"):
            if doc.get("folders") != dc_owned["folders"]:
                reasons.append("folders differ from the [workspace].repos projection")
                doc["folders"] = [dict(f) for f in dc_owned["folders"]]
                owned_stale = True
        drift.extend((label, r) for r in reasons)
        if not check and (expected != existing or owned_stale):
            parent[key_path[-1]] = expected
            _write_atomic(dst, json.dumps(doc, indent=2) + "\n")
    return drift


def main() -> int:
    parser = argparse.ArgumentParser(description="Synchronize .dotfiles SSOT to system overlays and IDE profiles")
    parser.add_argument("--check", action="store_true", help="Assert projections match SSOT without modifying disk")
    parser.add_argument("--client-surfaces", action="store_true",
                        help="Only the API-applied devcontainer.json / *.code-workspace settings blocks "
                             "(the tracked, drift-gated surfaces); skip the byte copies, mirror and HOME")
    parser.add_argument("--root", default=REPO_ROOT, help="Target repository root")
    args = parser.parse_args()

    root = os.path.abspath(args.root)
    drift = []

    # 1. Verify source existence
    for src in [VSCODE_SETTINGS_SRC, CODESERVER_SETTINGS_SRC]:
        if not os.path.isfile(src):
            return _fatal(f"Missing SSOT source: {src}", EXIT_MISSING_SOURCE)
    pol = load_policy()
    dc_owned = devcontainer_projection()
    drift.extend(project_edge_settings(args.check))
    # Terminal shortcuts are a native MiOS projection. Both editor profiles
    # must pass tmux's prefix through when the integrated terminal has focus.
    mobile = json.load(open(os.path.join(REPO_ROOT, "usr/share/mios/keybindings/vscode-settings.json"), encoding="utf-8"))
    for source in (VSCODE_SETTINGS_SRC, CODESERVER_SETTINGS_SRC):
        profile = json.load(open(source, encoding="utf-8"))
        updated = dict(profile, **mobile)
        if updated != profile:
            drift.append((source, "terminal shortcut settings differ from [keybindings] projection"))
            if not args.check:
                _write_atomic(source, json.dumps(updated, indent=2) + "\n")
    with open(VSCODE_SETTINGS_SRC, "r", encoding="utf-8") as f:
        vscode_ssot = json.load(f)
    with open(CODESERVER_SETTINGS_SRC, "r", encoding="utf-8") as f:
        codeserver_ssot = json.load(f)
    drift.extend(_ssot_unregistered(pol, vscode_ssot, ".dotfiles/vscode/settings.json"))
    drift.extend(_ssot_unregistered(pol, codeserver_ssot, ".dotfiles/code-server/settings.json"))

    if not args.client_surfaces:
        # 1b. Former byte copies of the rendered code-server stylesheet
        for rel in STALE_CSS_COPIES:
            stale = os.path.join(root, rel)
            if os.path.isfile(stale):
                drift.append((rel, "stale copy of the rendered stylesheet -- the one copy is "
                                   "usr/share/mios/themes/code-server-terminal.css (mios-dotfiles-render)"))
                if not args.check:
                    os.unlink(stale)

        # 2. Check or project mapped files (byte copies: the full desktop profile)
        for src, rel_target in TARGET_PROJECTIONS:
            dst = os.path.join(root, rel_target)
            src_bytes = open(src, "rb").read()
            if os.path.isfile(dst):
                dst_bytes = open(dst, "rb").read()
                if src_bytes != dst_bytes:
                    drift.append((rel_target, "byte copy differs from the SSOT source"))
            else:
                drift.append((rel_target, "byte copy is missing"))

            if not args.check:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                with open(dst, "wb") as f:
                    f.write(src_bytes)

        # 3. Mirror .dotfiles to usr/share/mios/dotfiles
        share_dotfiles = os.path.join(root, "usr/share/mios/dotfiles")
        if not args.check:
            os.makedirs(share_dotfiles, exist_ok=True)
            for dirpath, _, filenames in os.walk(DOTFILES_DIR):
                rel = os.path.relpath(dirpath, DOTFILES_DIR)
                target_dir = os.path.join(share_dotfiles, rel) if rel != "." else share_dotfiles
                os.makedirs(target_dir, exist_ok=True)
                for fn in filenames:
                    s_file = os.path.join(dirpath, fn)
                    d_file = os.path.join(target_dir, fn)
                    try:
                        shutil.copy2(s_file, d_file)
                    except PermissionError:
                        shutil.copyfile(s_file, d_file)

        # 4. Also project into active user home directories if write mode
        if not args.check:
            home = os.environ.get("HOME", "")
            if home and os.path.isdir(home):
                home_targets = [
                    (VSCODE_SETTINGS_SRC, os.path.join(home, ".config/Code/User/settings.json")),
                    (VSCODE_SETTINGS_SRC, os.path.join(home, ".vscode-server/data/Machine/settings.json")),
                    (VSCODE_SETTINGS_SRC, os.path.join(home, ".vscode-server/data/User/settings.json")),
                    (VSCODE_SETTINGS_SRC, os.path.join(home, ".vscode-remote/data/Machine/settings.json")),
                    (VSCODE_SETTINGS_SRC, os.path.join(home, ".vscode-remote/data/User/settings.json")),
                    (VSCODE_SETTINGS_SRC, os.path.join(home, ".vscode-server-insiders/data/Machine/settings.json")),
                    (VSCODE_SETTINGS_SRC, os.path.join(home, ".vscode-server-insiders/data/User/settings.json")),
                    (CODESERVER_SETTINGS_SRC, os.path.join(home, ".local/share/code-server/User/settings.json")),
                ]
                for s, d in home_targets:
                    if os.path.isdir(os.path.dirname(d)):
                        try:
                            shutil.copy2(s, d)
                        except Exception:
                            pass
            # Sync theme extension to skeletons and home profiles
            theme_src = os.path.join(root, "usr/share/mios/extensions/mios-theme-mobile")
            if os.path.isdir(theme_src):
                skel_targets = [
                    os.path.join(root, "etc/skel/.vscode/extensions/mios-theme-mobile"),
                    os.path.join(root, "etc/skel/.local/share/code-server/extensions/mios-theme-mobile"),
                ]
                for st in skel_targets:
                    try:
                        os.makedirs(os.path.dirname(st), exist_ok=True)
                        if os.path.exists(st):
                            if os.path.islink(st):
                                os.unlink(st)
                            elif os.path.isdir(st):
                                shutil.rmtree(st)
                        shutil.copytree(theme_src, st)
                    except Exception:
                        pass

                home = os.environ.get("HOME", "")
                if home and os.path.isdir(home):
                    user_ext_targets = [
                        os.path.join(home, ".vscode-server/extensions/mios-theme-mobile"),
                        os.path.join(home, ".vscode-server-insiders/extensions/mios-theme-mobile"),
                        os.path.join(home, ".vscode-remote/extensions/mios-theme-mobile"),
                        os.path.join(home, ".vscode-remote-insiders/extensions/mios-theme-mobile"),
                        os.path.join(home, ".local/share/code-server/extensions/mios-theme-mobile"),
                    ]
                    for ut in user_ext_targets:
                        try:
                            if os.path.isdir(os.path.dirname(ut)):
                                if os.path.exists(ut):
                                    if os.path.islink(ut):
                                        os.unlink(ut)
                                    elif os.path.isdir(ut):
                                        shutil.rmtree(ut)
                                shutil.copytree(theme_src, ut)
                        except Exception:
                            pass


    # 5. Merge the client-portable SSOT subset into every devcontainer.json /
    #    *.code-workspace and prune what a connecting client may not register.
    drift.extend(project_json_merges(args.check, pol, vscode_ssot, dc_owned))

    if args.check:
        if drift:
            for label, reason in drift:
                print(f"[sync-dotfiles] DRIFT {label}: {reason}", file=sys.stderr)
            files = sorted({label for label, _ in drift})
            print(f"[sync-dotfiles] Drift detected in {len(files)} files: {', '.join(files)}", file=sys.stderr)
            return EXIT_DRIFT
        print("[sync-dotfiles] All .dotfiles projections in sync.")
        return 0

    # Write mode: the SSOT itself carrying a key VS Code does not know is the one
    # drift the projection cannot repair, so it is reported and fails the run.
    unfixable = [(label, reason) for label, reason in drift
                 if label.startswith(".dotfiles/") and reason.startswith("unregistered key")]
    if unfixable:
        for label, reason in unfixable:
            print(f"[sync-dotfiles] DRIFT {label}: {reason}", file=sys.stderr)
        return EXIT_DRIFT
    what = ("the client-portable surfaces" if args.client_surfaces
            else f"{len(TARGET_PROJECTIONS)} targets + the client-portable surfaces")
    print(f"[sync-dotfiles] Successfully synchronized .dotfiles SSOT to {what}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
