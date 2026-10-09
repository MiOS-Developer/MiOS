# AI-hint: The single shared Python resolver for the layered mios.toml SSOT -- the Python peer of tools/lib/userenv.sh.
# AI-doc: usr/share/doc/mios/manual/mios.md
"""Shared layered mios.toml resolver (vendor < host < user) + canonical palette defaults."""

from __future__ import annotations

import glob
import os
import re

try:
    import tomllib as _toml
except ImportError:  # py < 3.11
    try:
        import tomli as _toml  # type: ignore
    except ImportError:  # pragma: no cover
        _toml = None

def _default_vendor(root=""):
    """Vendor mios.toml path when no explicit override is set: root-relative when
    MIOS_TOML_ROOT is given; else the FHS install if present; else repo-relative to
    THIS file (source checkout / CI unit tests, where /usr/share/mios is absent) so
    SSOT reads still resolve. Only the PATH is derived here -- no value is hardcoded."""
    if root:
        return os.path.join(root, "usr/share/mios/mios.toml")
    fhs = "/usr/share/mios/mios.toml"
    if os.path.exists(fhs):
        return fhs
    return os.path.normpath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "..", "share", "mios", "mios.toml"))

_ROOT = os.environ.get("MIOS_TOML_ROOT", "")

def get_version(key: str, default: str = "") -> str:
    """Return a SSOT component version from [versions] (task_2214)."""
    data = load_merged()
    vers = data.get("versions") or {}
    return str(vers.get(key, default))

def _frags(dirpath):
    """The *.toml drop-in fragments in dirpath, sorted lexically by BASENAME
    (systemd .d ordering), or [] if the dir is absent. A missing mios.d/ makes
    this a no-op -- the whole drop-in layer collapses to nothing."""
    if not dirpath or not os.path.isdir(dirpath):
        return []
    return sorted(glob.glob(os.path.join(dirpath, "*.toml")),
                  key=os.path.basename)

def _tier_dirs():
    """(vendor, vendor_d, host, host_d, user, user_d) resolved from the env at
    CALL time. Vendor FRAGMENTS live in /usr/lib/mios/mios.d (Law 1 USR-OVER-ETC
    + systemd's /usr/lib vendor convention), NOT beside the /usr/share monolith;
    admin/user fragments sit in a mios.d/ beside their monolith."""
    root = os.environ.get("MIOS_TOML_ROOT", "")
    vendor = os.environ.get("MIOS_VENDOR_TOML") or os.environ.get("MIOS_TOML") or _default_vendor(root)
    host = os.environ.get("MIOS_HOST_TOML", "/etc/mios/mios.toml")
    user = os.environ.get("MIOS_USER_TOML") or os.path.join(
        os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "mios/mios.toml")
    vendor_d = os.environ.get("MIOS_VENDOR_TOML_D") or (
        os.path.join(root, "usr/lib/mios/mios.d") if root else "/usr/lib/mios/mios.d")
    host_d = os.environ.get("MIOS_HOST_TOML_D") or os.path.join(os.path.dirname(host), "mios.d")
    user_d = os.environ.get("MIOS_USER_TOML_D") or os.path.join(os.path.dirname(user), "mios.d")
    return vendor, vendor_d, host, host_d, user, user_d

def layer_paths():
    """The overlay layer paths, lowest precedence first, EXPANDED to include
    drop-in fragments. Resolved from the environment at CALL time (not import
    time) so a caller / test / CI on a non-FHS host can retarget a layer via
    MIOS_VENDOR_TOML / MIOS_HOST_TOML / MIOS_USER_TOML / MIOS_TOML_ROOT (and the
    *_TOML_D fragment-dir overrides) AFTER this module is imported.

    Ordering is TIER-MAJOR (vendor < host < user); within each tier the monolith
    seeds LOWEST, then that tier's mios.d/*.toml fragments (lexical basename)
    deep-merge over it. Tier is the primary precedence key -- a vendor fragment
    can NEVER outrank a higher tier (the XDG/git-config scope model, not
    systemd's global flat sort). NO-OP when no mios.d/ exists: every _frags()
    glob is empty and this returns exactly [vendor, host, user] as before."""
    vendor, vendor_d, host, host_d, user, user_d = _tier_dirs()
    return ([vendor] + _frags(vendor_d)
            + [host] + _frags(host_d)
            + [user] + _frags(user_d))

def deep_merge(dst, src):
    """Recursively merge src into dst. Non-empty scalars/lists overwrite; an empty
    string never overrides a non-empty value below it (the mios.toml overlay rule)."""
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            deep_merge(dst[k], v)
        elif isinstance(v, str) and v == "" and dst.get(k) not in (None, ""):
            continue
        else:
            dst[k] = v
    return dst

def _load_one(path):
    if not path or not os.path.isfile(path) or _toml is None:
        return {}
    try:
        with open(path, "rb") as fh:
            return _toml.load(fh)
    except Exception:  # noqa: BLE001 -- a broken overlay layer must not crash a reader
        return {}

import json
import shutil
import subprocess

def _native_resolver_json():
    if os.environ.get("MIOS_RESOLVER_NATIVE") == "0":
        return None
    binary = shutil.which("mios-resolver")
    if not binary:
        return None
    try:
        res = subprocess.run([binary, "--emit=json"], capture_output=True, text=True, timeout=5)
        if res.returncode == 0:
            return json.loads(res.stdout)
    except Exception:
        pass
    return None

_LOAD_MERGED_CACHE = None

def clear_cache():
    global _LOAD_MERGED_CACHE
    _LOAD_MERGED_CACHE = None

def derive_ports(merged):
    """Allocate every [ports] value from the [ports.categories] schema, IN PLACE.

    This runs AFTER layer merging, so it is the live runtime allocator: a
    factory/OEM default in the vendor mios.toml, an operator override in
    /etc/mios/mios.toml, or a user override in ~/.config/mios/mios.toml all feed
    the same derivation and the result is what every consumer sees -- userenv.sh
    exports, /etc/mios/install.env, the Quadlet render, the firewall phases and
    the Containerfile build args.

    A member's port is  base + index_in_members * stride.  Because `members` is
    ordered, adding or removing a service reallocates the category with no hand
    edit and no chance of a collision. `pinned` entries are protocol contracts
    (DNS/53) and are emitted verbatim.

    The flat [ports] table in the vendor file is a rendered projection kept for
    readability and drift-gating; the derivation OVERRIDES it, so an operator who
    retargets a category base is never silently beaten by a stale vendor literal.
    """
    ports = merged.get("ports")
    if not isinstance(ports, dict):
        return merged
    cats = ports.get("categories")
    if not isinstance(cats, dict):
        return merged

    for _cat, cfg in sorted(cats.items()):
        if not isinstance(cfg, dict):
            continue
        try:
            base = int(cfg.get("base", 0))
            stride = int(cfg.get("stride", 1))
        except (TypeError, ValueError):
            continue
        members = cfg.get("members")
        if isinstance(members, list):
            for idx, member in enumerate(members):
                if isinstance(member, str) and member:
                    ports[member] = base + idx * stride
        pinned = cfg.get("pinned")
        if isinstance(pinned, dict):
            for name, value in pinned.items():
                try:
                    ports[name] = int(value)
                except (TypeError, ValueError):
                    continue
    return merged

def load_merged(layers=None):
    """Full three-layer overlay (vendor < host < user), highest wins."""
    global _LOAD_MERGED_CACHE
    if layers is None and _LOAD_MERGED_CACHE is not None:
        return _LOAD_MERGED_CACHE

    # Native resolution supplies the file-layer baseline; the DB overlay and
    # derived ports still apply before caching, just as in the Python fallback.
    nat = _native_resolver_json() if layers is None else None
    if isinstance(nat, dict) and isinstance(nat.get("merged"), dict):
        merged = nat["merged"]
    else:
        merged = {}
        for p in (layers if layers is not None else layer_paths()):
            deep_merge(merged, _load_one(p))

    if layers is None:
        try:
            import mios_db_config
            if mios_db_config.is_db_authoritative():
                db_cfg = mios_db_config.load_db_config()
                if db_cfg:
                    orig_packages = merged.get("packages")
                    deep_merge(merged, db_cfg)
                    if orig_packages:
                        if "packages" not in merged or not isinstance(merged["packages"], dict) or not merged["packages"]:
                            merged["packages"] = orig_packages
                        else:
                            for pkg_name, pkg_data in orig_packages.items():
                                if pkg_name not in merged["packages"]:
                                    merged["packages"][pkg_name] = pkg_data
        except Exception:
            pass

    # Allocate ports from [ports.categories] AFTER every layer (and the DB
    # overlay) has merged, so operator/user overrides of a category base or
    # member list re-derive live instead of losing to the vendor flat table.
    derive_ports(merged)

    if layers is None:
        _LOAD_MERGED_CACHE = merged
    return merged

def load_vendor():
    """Vendor-only view (monolith + /usr/lib/mios/mios.d fragments) -- what the
    offline drift-gates intentionally read. Includes vendor fragments so that a
    section migrated out of the monolith into a mios.d/ fragment (R6) stays
    visible to the gates. No-op vs the old monolith-only view until the first
    vendor fragment exists."""
    vendor, vendor_d, *_ = _tier_dirs()
    merged = {}
    deep_merge(merged, _load_one(vendor))
    for p in _frags(vendor_d):
        deep_merge(merged, _load_one(p))
    return merged

def section(data, name):
    """A [table] (or dotted [a.b]) sub-dict, or {} if absent."""
    cur = data
    for part in name.split("."):
        if not isinstance(cur, dict):
            return {}
        cur = cur.get(part, {})
    return cur if isinstance(cur, dict) else {}

def get(sect, key, default=None, data=None):
    """One [sect].key value from the merged overlay (or a supplied `data`)."""
    if data is None:
        try:
            import mios_db_config
            if mios_db_config.is_db_authoritative():
                db_cfg = mios_db_config.load_db_config()
                if db_cfg:
                    sect_dict = section(db_cfg, sect)
                    if key in sect_dict:
                        return sect_dict[key]
        except Exception:
            pass

    d = data if data is not None else load_merged()
    return section(d, sect).get(key, default)

PALETTE_DEFAULTS = {
    "bg": "#282262", "fg": "#E7DFD3", "accent": "#1A407F", "cursor": "#F35C15",
    "success": "#3E7765", "warning": "#F35C15", "error": "#DC271B", "info": "#1A407F",
    "muted": "#948E8E", "subtle": "#B7C9D7", "earth": "#734F39", "silver": "#E0E0E0",
    "ansi_0_black": "#282262", "ansi_1_red": "#DC271B", "ansi_2_green": "#3E7765",
    "ansi_3_yellow": "#F35C15", "ansi_4_blue": "#1A407F", "ansi_5_magenta": "#734F39",
    "ansi_6_cyan": "#B7C9D7", "ansi_7_white": "#E7DFD3", "ansi_8_bright_black": "#948E8E",
    "ansi_9_bright_red": "#FF6B5C", "ansi_10_bright_green": "#5FAA8E",
    "ansi_11_bright_yellow": "#FF8540", "ansi_12_bright_blue": "#3D6BA8",
    "ansi_13_bright_magenta": "#9D7660", "ansi_14_bright_cyan": "#E0E0E0",
    "ansi_15_bright_white": "#FFFFFF",
}

def colors(data=None):
    """Resolved palette: mios.toml [colors] over PALETTE_DEFAULTS (SSOT wins)."""
    if data is None:
        try:
            import mios_db_config
            if mios_db_config.is_db_authoritative():
                db_cfg = mios_db_config.load_db_config()
                if db_cfg:
                    c = section(db_cfg, "colors")
                    return {k: str(c.get(k, v)) for k, v in PALETTE_DEFAULTS.items()}
        except Exception:
            pass

    c = section(data if data is not None else load_merged(), "colors")
    return {k: str(c.get(k, v)) for k, v in PALETTE_DEFAULTS.items()}

def float_allowlist(data=None):
    """Resolved float allowlist table from mios.toml [build.float]."""
    d = data if data is not None else load_vendor()
    return section(d, "build.float")

class EdgeInsetsError(ValueError):
    """[theme].padding / [theme].scrollbar_state outside the grammar MiOS accepts."""

# The non-negative integer subset of the Windows Terminal profile `padding` grammar.
_WT_PADDING = re.compile(r"([0-9]+)(?: *, *([0-9]+)(?: *, *([0-9]+) *, *([0-9]+))?)?")  # fullmatch only
_WT_SCROLLBAR_STATES = ("visible", "hidden", "always")

def edge_insets(data):
    """The ONE parser of the terminal edge intent: {left, top, right, bottom,
    scrollbar_hidden} from [theme].padding ("#", "#, #" = left-right then
    top-bottom, or "#, #, #, #" = left, top, right, bottom) and
    [theme].scrollbar_state. Parity table: usr/share/mios/theme/fixtures/edge/padding-cases.tsv."""
    theme = section(data, "theme")
    raw = theme.get("padding")
    m = None if isinstance(raw, bool) or raw is None else _WT_PADDING.fullmatch(str(raw))
    if not m:
        raise EdgeInsetsError(f"[theme].padding: '{'' if raw is None else raw}' is not a non-negative integer WT padding")
    a, b, c, d = m.groups()
    if b is None:
        left = top = right = bottom = int(a)
    elif c is None:
        left = right = int(a)
        top = bottom = int(b)
    else:
        left, top, right, bottom = int(a), int(b), int(c), int(d)
    state = theme.get("scrollbar_state")
    if state not in _WT_SCROLLBAR_STATES:
        raise EdgeInsetsError(f"[theme].scrollbar_state: '{state}' is not one of "
                              + ", ".join(_WT_SCROLLBAR_STATES))
    return {"left": left, "top": top, "right": right, "bottom": bottom,
            "scrollbar_hidden": state == "hidden"}

def vendor_tree(root):
    """Vendor tier (monolith + usr/lib/mios/mios.d) of the tree at root; MIOS_VENDOR_TOML[_D] override it."""
    vendor = os.environ.get("MIOS_VENDOR_TOML") or os.path.join(root, "usr", "share", "mios", "mios.toml")
    frag_dir = os.environ.get("MIOS_VENDOR_TOML_D") or os.path.join(root, "usr", "lib", "mios", "mios.d")
    return load_merged([vendor] + _frags(frag_dir))

def write_atomic(path, text):
    """Temp file + rename beside path, keeping an existing file's mode (0644 when new)."""
    import stat
    import tempfile
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    mode = stat.S_IMODE(os.stat(path).st_mode) if os.path.exists(path) else 0o644
    fd, tmp = tempfile.mkstemp(dir=parent, prefix=".mios-write.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

def golden_diff(path, rendered):
    """None when path holds exactly rendered, else one line naming the file and its first differing line."""
    try:
        with open(path, encoding="utf-8", newline="") as fh:
            committed = fh.read()
    except OSError as exc:
        return f"{path}: cannot read the committed golden ({exc.strerror}); run --write-fixture"
    if committed == rendered:
        return None
    old, new = committed.splitlines() + ["<end of file>"], rendered.splitlines() + ["<end of file>"]
    i = next((i for i, pair in enumerate(zip(old, new)) if pair[0] != pair[1]), None)
    return f"{path}: differs from the generator only in line endings" if i is None else (
        f"{path}:{i + 1}: committed {old[i]!r}, generator renders {new[i]!r}")

def golden_gate(tag, root, renders, write=False):
    """Regenerate (write) or diff every root-relative path in renders; 1 after printing each drift to stderr."""
    import sys
    drift = 0
    for rel, text in renders.items():
        path = os.path.join(root, rel)
        if write:
            write_atomic(path, text)
        elif (msg := golden_diff(path, text)):
            print(f"[{tag}] DRIFT {msg}", file=sys.stderr)
            drift = 1
    return drift

def canonical_name(dotted_path: str) -> str:
    """Lexical peer of mios_resolver.names.canonical_name."""
    body = re.sub(r"[^A-Z0-9_]", "_", dotted_path.upper())
    return body if body.startswith("MIOS_") else "MIOS_" + body


def get_aliases(dotted_path):
    aliases = []

    if dotted_path.startswith("converge."):
        aliases.append("MIOS_CONV_" + dotted_path[len("converge."):].upper().replace(".", "_").replace("-", "_"))
    elif dotted_path.startswith("ai.vllm."):
        suffix = dotted_path[len("ai.vllm."):].upper().replace(".", "_").replace("-", "_")
        if suffix == "V1_ENGINE":
            aliases.append("MIOS_VLLM_USE_V1")
        else:
            aliases.append(f"MIOS_VLLM_{suffix}")
    elif dotted_path.startswith("ai.sglang."):
        suffix = dotted_path[len("ai.sglang."):].upper().replace(".", "_").replace("-", "_")
        if suffix == "UNIFIED_RADIX_TREE":
            aliases.append("MIOS_SGLANG_ENABLE_UNIFIED_RADIX_TREE")
        elif suffix == "HIERARCHICAL_CACHE":
            aliases.append("MIOS_SGLANG_ENABLE_HIERARCHICAL_CACHE")
        else:
            aliases.append(f"MIOS_SGLANG_{suffix}")

    elif dotted_path == "identity.username":
        aliases.extend(["MIOS_USER", "MIOS_DEFAULT_USER"])
    elif dotted_path == "identity.uid":
        aliases.extend(["MIOS_UID", "MIOS_USER_UID"])
    elif dotted_path == "identity.fullname":
        aliases.append("MIOS_USER_FULLNAME")
    elif dotted_path == "identity.hostname":
        aliases.extend(["MIOS_HOSTNAME", "MIOS_DEFAULT_HOST"])
    elif dotted_path == "identity.shell":
        aliases.extend(["MIOS_USER_SHELL", "MIOS_DEFAULT_SHELL"])
    elif dotted_path == "identity.groups":
        aliases.extend(["MIOS_USER_GROUPS", "MIOS_DEFAULT_GROUPS"])
    elif dotted_path == "identity.default_password":
        aliases.append("MIOS_DEFAULT_PASSWORD")
    elif dotted_path == "accounts.db_backed":
        aliases.append("MIOS_ACCOUNTS_DB_BACKED")

    elif dotted_path == "locale.timezone":
        aliases.extend(["MIOS_TIMEZONE", "MIOS_DEFAULT_TIMEZONE"])
    elif dotted_path == "locale.keyboard_layout":
        aliases.extend(["MIOS_KEYBOARD", "MIOS_DEFAULT_KEYBOARD"])
    elif dotted_path == "locale.language":
        aliases.extend(["MIOS_LOCALE", "MIOS_DEFAULT_LOCALE"])

    elif dotted_path == "auth.ssh_key_action":
        aliases.append("MIOS_SSH_KEY_ACTION")
    elif dotted_path == "auth.password_policy":
        aliases.append("MIOS_PASSWORD_POLICY")

    elif dotted_path == "network.firewalld_default_zone":
        aliases.append("MIOS_FIREWALLD_ZONE")

    elif dotted_path.startswith("portal."):
        suffix = dotted_path[len("portal."):].upper().replace(".", "_").replace("-", "_")
        if suffix == "PUBLIC_HOST":
            aliases.append("MIOS_PUBLIC_HOST")
        else:
            aliases.append(f"MIOS_PORTAL_{suffix}")

    elif dotted_path.startswith("a2a."):
        name = dotted_path[len("a2a."):].upper().replace(".", "_")
        if name == "DISCOVER_PORT":
            aliases.append("MIOS_A2A_DISCOVER_PORT")
        elif name == "PUBLIC_DOMAIN":
            aliases.append("MIOS_PUBLIC_DOMAIN")
        else:
            aliases.append(f"MIOS_A2A_{name}")

    elif dotted_path == "agents.hermes.endpoint":
        aliases.append("MIOS_HERMES_WORKER_ENDPOINT")

    elif dotted_path.startswith("ai.") and not dotted_path.startswith("ai.vllm.") and not dotted_path.startswith("ai.sglang."):
        suffix = dotted_path[len("ai."):].upper().replace(".", "_").replace("-", "_")
        if suffix == "API_KEY" or suffix == "KEY":
            aliases.append("MIOS_AI_KEY")
        elif suffix == "EMBED_MODEL":
            aliases.append("MIOS_VERB_EMBED_MODEL")
        elif suffix == "STACK_MODEL":
            aliases.append("MIOS_STACK_MODEL")
        elif suffix == "CHAT_VISION_MODEL":
            aliases.append("MIOS_AGENT_PIPE_VISION_MODEL")
        elif suffix == "AGENT_VENV":
            aliases.append("MIOS_HERMES_VENV")
        elif suffix == "AGENT_INSTALL_DIR":
            aliases.append("MIOS_HERMES_DIR")
        elif suffix == "MICRO_MODEL":
            aliases.append("MIOS_MICRO_MODEL")
        elif suffix == "MICRO_ENDPOINT":
            aliases.append("MIOS_MICRO_ENDPOINT")
        elif suffix == "OPENCODE_GATEWAY_WORKDIR":
            aliases.append("MIOS_OPENCODE_WORKDIR")
        elif suffix == "OPENCODE_GATEWAY_TIMEOUT_S":
            aliases.append("MIOS_OPENCODE_TIMEOUT_S")
        elif suffix.startswith("OPENCODE_"):
            aliases.append(f"MIOS_{suffix}")
        elif suffix in {"ENDPOINT", "MODEL"}:
            aliases.extend([f"MIOS_AI_{suffix}", f"MIOS_{suffix}"])
        elif suffix in {"SYSTEM_PROMPT_FILE", "TOKENIZER_BACKEND",
                        "TOKENIZER_ENCODING", "TOKENIZER_CACHE_DIR", "TOKENIZER_PATH",
                        "HERMES_AGENT_REPO", "HERMES_AGENT_REF", "HERMES_BACKEND_URL",
                        "MCP_REGISTRY"}:
            aliases.append(f"MIOS_{suffix}")

    elif dotted_path.startswith("build."):
        name = dotted_path[len("build."):].upper().replace(".", "_")
        # AI_RAM_FLOOR_GB is deliberately absent: [ai].ram_floor_gb owns that
        # name already, with a different value (task_964; aliases.rs twin).
        if name in {"LOCAL_TAG", "RECHUNK_MAX_LAYERS"}:
            aliases.append(f"MIOS_{name}")
        else:
            aliases.append(f"MIOS_BUILD_{name}")

    elif dotted_path.startswith("code_mode."):
        name = dotted_path[len("code_mode."):].upper()
        aliases.append(f"MIOS_CODEMODE_{name}")

    elif dotted_path.startswith("colors."):
        name = dotted_path[len("colors."):].upper()
        if name.startswith("ANSI_"):
            aliases.append(f"MIOS_{name}")
        else:
            aliases.append(f"MIOS_COLOR_{name}")

    elif dotted_path.startswith("frontier."):
        name = dotted_path[len("frontier."):].upper()
        if name == "STREAM_TO_REASONING":
            aliases.append("MIOS_A2O_STREAM_REASONING")
        else:
            aliases.append(f"MIOS_A2O_{name}")

    elif dotted_path.startswith("paths."):
        name = dotted_path[len("paths."):].upper()
        if name == "MIOS_TOML":
            aliases.append("MIOS_TOML")
        elif name == "WSL_FIRSTBOOT_DONE":
            aliases.append("MIOS_WSLBOOT_DONE")
        elif name in ("LAUNCHER_SOCKET", "MIOS_LAUNCHER_SOCKET"):
            aliases.append("MIOS_LAUNCHER_SOCKET")
        else:
            aliases.append(f"MIOS_{name}")

    elif dotted_path.startswith("units."):
        # [units].forge -> MIOS_UNIT_FORGE (the name every consumer already uses).
        # Unit names carry '-' and '.' (var-lib-nfs-rpc_pipefs.mount), neither of
        # which is legal in a shell identifier -- left raw, `export` rejects the
        # whole assignment and the var is silently never emitted.
        tail = dotted_path[len("units."):].upper()
        aliases.append("MIOS_UNIT_" + re.sub(r"[^A-Z0-9]", "_", tail))

    elif dotted_path.startswith("urls."):
        # [urls].forge -> MIOS_FORGE_URL; the two repo URLs keep their own shape.
        name = dotted_path[len("urls."):].upper()
        # task_957: non_addressable is a list of port KEYS, not a URL, and no
        # consumer reads either spelling. REPO/BOOTSTRAP_REPO matched the default.
        if name == "LOCAL_FORGE_REPO":
            aliases.append("MIOS_LOCAL_FORGE_REPO")
        elif name != "NON_ADDRESSABLE":
            aliases.append(f"MIOS_{name}_URL")

    elif dotted_path.startswith("pgvector.") or dotted_path.startswith("pg."):
        prefix_len = len("pgvector.") if dotted_path.startswith("pgvector.") else len("pg.")
        name = dotted_path[prefix_len:].upper().replace(".", "_").replace("-", "_")
        if name == "DB_BACKEND":
            aliases.append("MIOS_DB_BACKEND")
        elif name == "RLS_ENABLE":
            aliases.append("MIOS_DB_RLS_ENABLE")
        else:
            aliases.append(f"MIOS_PG_{name}")
            aliases.append(f"MIOS_PGVECTOR_{name}")

    elif dotted_path.startswith("routing.") and not dotted_path.startswith("routing.domains."):
        name = dotted_path[len("routing."):].upper().replace(".", "_")
        aliases.append(f"MIOS_{name}")

    elif dotted_path == "polish.timeout_seconds":
        aliases.append("MIOS_POLISH_TIMEOUT_S")

    elif dotted_path == "refine.timeout_seconds":
        aliases.append("MIOS_REFINE_TIMEOUT_S")

    elif dotted_path == "security.fapolicyd_observe.enable":
        aliases.append("MIOS_FAPOLICYD_OBSERVE_ENABLE")

    elif dotted_path == "uki.verity_uki_build":
        aliases.append("MIOS_UKI_VERITY_BUILD")

    elif dotted_path.startswith("verity."):
        name = dotted_path[len("verity."):].upper()
        aliases.append(f"MIOS_{name}")

    elif dotted_path == "user.hostname":
        aliases.append("MIOS_HOSTNAME")

    elif dotted_path == "user.name":
        aliases.append("MIOS_USER_FULLNAME")

    elif dotted_path == "flatpaks.install":
        aliases.append("MIOS_FLATPAKS")

    elif dotted_path.startswith("llamacpp."):
        name = dotted_path[len("llamacpp."):].upper()
        if name == "CPU_NODE_THREADS":
            aliases.append("MIOS_CPU_NODE_THREADS")
        else:
            aliases.append(f"MIOS_LLAMACPP_{name}")

    elif dotted_path == "meta.mios_version":
        aliases.append("MIOS_VERSION")

    elif dotted_path.startswith("network.quadlet."):
        name = dotted_path[len("network.quadlet."):].upper()
        if name == "CORE_GATEWAY":
            aliases.append("MIOS_CORE_NET_GATEWAY")
        elif name == "CORE_SUBNET":
            aliases.append("MIOS_CORE_NET_SUBNET")
        elif name == "NETWORK":
            aliases.append("MIOS_QUADLET_NETWORK")
        elif name == "SUBNET":
            aliases.append("MIOS_QUADLET_SUBNET")

    elif dotted_path == "fs_watcher.watch_dirs":
        aliases.append("MIOS_FS_WATCHER_DIRS")

    elif dotted_path.startswith("ports.categories."):
        # Allocation SCHEMA (base/stride/members/pinned), not a port. Emitting
        # MIOS_PORT_CATEGORIES_<CAT>_BASE would invent ports that have no
        # [ports].<name> key and trip the globals-parity gate. The derived
        # values themselves are emitted from the flat [ports] keys.
        pass

    elif dotted_path.startswith("ports."):
        name = dotted_path[len("ports."):].upper().replace(".", "_").replace("-", "_")
        # MIOS_<x>_PORT -- alongside the section-generic MIOS_PORTS_<x>, so NO consumer
        _canon = {"GUACAMOLE_WEB": "GUACAMOLE"}.get(name, name)
        aliases.extend([f"MIOS_PORT_{_canon}", f"MIOS_{_canon}_PORT"])

    elif dotted_path.startswith("image.sidecars."):
        name = dotted_path[len("image.sidecars."):].upper().replace(".", "_").replace("-", "_")
        if name.endswith("_VERSION"):
            base = name[:-len("_VERSION")]
            aliases.append(f"MIOS_{base}_VERSION")
        else:
            aliases.extend([f"MIOS_{name}_IMAGE", f"MIOS_{name}_VERSION"])

    elif dotted_path.startswith("services."):
        parts = dotted_path.split(".")
        if len(parts) >= 3:
            service = parts[1].upper().replace("-", "_")
            key = "_".join(parts[2:]).upper().replace("-", "_")
            if service == "WEBTOOLS":
                if key in {"USER", "UID", "GID"}:
                    aliases.append(f"MIOS_WEBTOOLS_{key}")
                elif key == "CDP_URL":
                    aliases.append("MIOS_CRAWL_CDP_URL")
                elif key == "CAMOUFOX":
                    aliases.append("MIOS_CRAWL_CAMOUFOX")
                elif key == "MIN_CHARS":
                    aliases.append("MIOS_CRAWL_MIN_CHARS")
                elif key.startswith("FIRECRAWL_") or key.startswith("CRAWL4AI_"):
                    aliases.append(f"MIOS_{key}")
            else:
                aliases.append(f"MIOS_{service}_{key}")

    elif dotted_path.startswith("storage.cephfs."):
        key = dotted_path[len("storage.cephfs."):].upper().replace(".", "_").replace("-", "_")
        if key == "XDG_CACHE_HOME_OVERRIDE":
            aliases.append("MIOS_XDG_CACHE_LOCAL_PATH")
        else:
            aliases.append(f"MIOS_CEPHFS_{key}")

    elif dotted_path.startswith("wsl2."):
        key = dotted_path[len("wsl2."):].upper().replace(".", "_").replace("-", "_")
        if key == "DESKTOP_COMPAT_GDK_BACKEND":
            aliases.append("MIOS_WSLG_GDK_BACKEND")
        elif key == "DESKTOP_COMPAT_MOZ_WAYLAND":
            aliases.append("MIOS_WSLG_MOZ_WAYLAND")
        elif key == "DESKTOP_COMPAT_QT_PLATFORM":
            aliases.append("MIOS_WSLG_QT_PLATFORM")
        elif key == "DEV_VM_QUADLET_NETWORK_MODE":
            aliases.append("MIOS_QUADLET_DEV_NETWORK_MODE")
        else:
            aliases.append(f"MIOS_WSL2_{key}")

    elif dotted_path.startswith("converge."):
        pass

    elif dotted_path.startswith("image.") and not dotted_path.startswith("image.sidecars."):
        key = dotted_path[len("image."):].upper().replace(".", "_").replace("-", "_")
        if key == "BRANCH":
            aliases.append("MIOS_BRANCH")
        elif key == "BASE":
            aliases.append("MIOS_BASE_IMAGE")
        elif key == "BIB":
            aliases.append("MIOS_BIB_IMAGE")
        elif key == "LOCAL_TAG":
            aliases.append("MIOS_LOCAL_TAG")
        elif key in {"REF", "NAME", "TAG"}:
            aliases.append(f"MIOS_IMAGE_{key}")

    elif dotted_path.startswith("versions."):
        key = dotted_path[len("versions."):].upper().replace(".", "_").replace("-", "_")
        aliases.append(f"MIOS_VERSION_{key}")

    elif dotted_path.startswith("desktop."):
        key = dotted_path[len("desktop."):].upper().replace(".", "_").replace("-", "_")
        if key == "COLOR_SCHEME":
            aliases.append("MIOS_COLOR_SCHEME")
        elif key == "FLATPAKS":
            aliases.append("MIOS_FLATPAKS")
        elif key == "SESSION":
            aliases.append(f"MIOS_DESKTOP_{key}")

    elif dotted_path.startswith("bootstrap.dev_vm."):
        key = dotted_path[len("bootstrap.dev_vm."):].upper().replace(".", "_").replace("-", "_")
        if key == "MACHINE_NAME":
            aliases.append("MIOS_BUILDER_DISTRO")
        elif key == "WSL_DISTRO":
            aliases.append("MIOS_WSL_DISTRO")
        elif key == "DISK_SIZE_GB":
            aliases.append("MIOS_DEV_VM_DISK_GB")
        elif key == "GPU_PASSTHROUGH":
            aliases.append("MIOS_DEV_VM_GPU")
        elif key == "HOST_RESERVE_CPU_PCT":
            aliases.append("MIOS_DEV_VM_CPU_RESERVE_PCT")
        elif key == "HOST_RESERVE_CPU_MIN":
            aliases.append("MIOS_DEV_VM_CPU_RESERVE_MIN")
        elif key == "HOST_RESERVE_MEMORY_PCT":
            aliases.append("MIOS_DEV_VM_MEMORY_RESERVE_PCT")
        elif key == "HOST_RESERVE_MEMORY_GB":
            aliases.append("MIOS_DEV_VM_MEMORY_RESERVE_GB")
        elif key == "HOST_RESERVE_DISK_GB":
            aliases.append("MIOS_DEV_VM_DISK_RESERVE_GB")
        elif key in {"BASE_IMAGE", "CPUS", "MEMORY_MB"}:
            aliases.append(f"MIOS_DEV_VM_{key}")
    elif dotted_path.startswith("bootstrap.host_storage."):
        key = dotted_path[len("bootstrap.host_storage."):].upper().replace(".", "_").replace("-", "_")
        if key == "SHRINK_MB":
            aliases.append("MIOS_DATA_DISK_MB")
        elif key == "DRIVE_LETTER":
            aliases.append("MIOS_DATA_DISK_LETTER")
    elif dotted_path.startswith("bootstrap.") and not dotted_path.startswith("bootstrap.dev_vm.") and not dotted_path.startswith("bootstrap.host_storage."):
        key = dotted_path[len("bootstrap."):].upper().replace(".", "_").replace("-", "_")
        if key == "MIOS_REPO":
            aliases.append("MIOS_REPO_URL")
        elif key == "BOOTSTRAP_REPO":
            aliases.append("MIOS_BOOTSTRAP_REPO_URL")
        elif key == "MODE":
            aliases.append(f"MIOS_BOOTSTRAP_{key}")

    elif dotted_path.startswith("reliability."):
        key = dotted_path[len("reliability."):].upper().replace(".", "_").replace("-", "_")
        aliases.append(f"MIOS_RELIABILITY_{key}")

    elif dotted_path.startswith("routing."):
        key = dotted_path[len("routing."):].upper().replace(".", "_").replace("-", "_")
        aliases.append(f"MIOS_ROUTING_{key}")
        if key.startswith("LAUNCH_"):
            aliases.append(f"MIOS_{key}")

    elif dotted_path.startswith("mios-find.") or dotted_path.startswith("mios_find."):
        prefix_len = len("mios-find.") if dotted_path.startswith("mios-find.") else len("mios_find.")
        key = dotted_path[prefix_len:].upper().replace(".", "_").replace("-", "_")
        aliases.append(f"MIOS_FIND_{key}")

    elif dotted_path.startswith("mios."):
        key = dotted_path[len("mios."):].upper().replace(".", "_").replace("-", "_")
        aliases.append(f"MIOS_{key}")

    elif dotted_path.startswith("code_server."):
        key = dotted_path[len("code_server."):].upper().replace(".", "_").replace("-", "_")
        aliases.append(f"MIOS_CODE_SERVER_{key}")

    elif dotted_path.startswith("offline.backup_") or dotted_path.startswith("offline.backup."):
        key = dotted_path.split("backup", 1)[1].lstrip("_.").upper().replace(".", "_").replace("-", "_")
        aliases.extend([f"MIOS_PG_BACKUP_{key}", f"MIOS_OFFLINE_BACKUP_{key}"])

    elif dotted_path.startswith("postgres.") or dotted_path.startswith("db.") or dotted_path.startswith("pgvector."):
        key = dotted_path.split(".", 1)[1].upper().replace(".", "_").replace("-", "_")
        aliases.extend([f"MIOS_DB_{key}", f"MIOS_POSTGRES_{key}", f"MIOS_PG_{key}", f"MIOS_PGVECTOR_{key}"])

    return aliases

def walk(d, prefix=""):
    results = []
    if not isinstance(d, dict):
        return results
    for k, v in d.items():
        path = f"{prefix}.{k}" if prefix else k
        if path == "routing.domains":
            continue
        if isinstance(v, dict):
            results.extend(walk(v, path))
        else:
            results.append((path, v))
    return results

def process_val(dotted, v, stack_offset=0):
    if isinstance(v, bool):
        return "true" if v else "false"
    if dotted.startswith("ports.") and dotted != "ports.stack_id":
        try:
            if int(v) != 53:
                return int(v) + stack_offset
        except (ValueError, TypeError):
            pass
    if isinstance(v, list):
        # A list of scalars stays comma-joined and unquoted, which is what every
        # consumer of a MIOS_*_LIST expects. A list of TABLES or of lists is
        # rendered as TOML inline syntax, matching mios-resolver.
        #
        # str(dict) gives a PYTHON REPR -- {'ordinal': '01', 'fatal': True} --
        # which only Python can parse and which disagreed with the Rust
        # resolver's TOML inline tables on 12 keys. That was the whole residual
        # of check_resolver_differential_parity, sitting exactly at its ceiling
        # of 12 with no headroom (task_3361). TOML wins because it is a real
        # format and because Rust is the destination tier.
        return ",".join(
            _toml_inline(x) if isinstance(x, (dict, list)) else str(x) for x in v
        )
    return v


def _toml_inline(v):
    """Render a value as TOML inline syntax, byte-compatible with the toml crate.

    Key order inside a table is alphabetical among scalars and arrays, with
    nested TABLES emitted last -- that is what the Rust serializer does, and the
    comparison is byte-for-byte, so the order is part of the contract.
    """
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        # The toml crate prefers a LITERAL string (single quotes, no escaping)
        # when the content contains a backslash, because a basic string would
        # have to double every one. MIOS_DOCS_SANITIZE_PATH_REWRITES carries
        # Windows paths, so this is the difference between 'C:\MiOS\' and
        # "C:\\MiOS\\" -- both valid TOML, only one byte-equal to the resolver.
        if "\\" in v and "'" not in v and "\n" not in v and "\r" not in v:
            return "'" + v + "'"
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, list):
        return "[" + ", ".join(_toml_inline(x) for x in v) + "]"
    if isinstance(v, dict):
        plain = [(k, x) for k, x in v.items() if not isinstance(x, dict)]
        tables = [(k, x) for k, x in v.items() if isinstance(x, dict)]
        parts = ["%s = %s" % (k, _toml_inline(x)) for k, x in sorted(plain)]
        parts += ["%s = %s" % (k, _toml_inline(x)) for k, x in sorted(tables)]
        return "{ " + ", ".join(parts) + " }"
    return str(v)

# The CI suite registry is read from the TOML directly by its own reader.
# Projecting it produced two dozen shell constants no consumer reads,
# including whole comma-joined suite lists, in both generated resolvers.
EXCLUDED_SECTIONS = {"containers", "verbs", "recipes", "packages", "dotfiles", "btop", "theme", "install_phases", "messages", "ci", "tests", "units", "generation"}
WALK_MOSTLY_DEAD = {"ai", "image", "bootstrap", "profile", "sandbox", "security"}
WALK_EMIT_KEEP = {
    "MIOS_AI_BAKE_MODELS", "MIOS_AI_DIR", "MIOS_AI_EMBED_MODEL", "MIOS_AI_ENDPOINT",
    "MIOS_AI_JOURNAL", "MIOS_AI_MCP_DIR", "MIOS_AI_MEMORY_DIR", "MIOS_AI_MODEL", "MIOS_AI_AGENT_MODEL",
    "MIOS_AI_MODELS_DIR", "MIOS_AI_RAM_FLOOR_GB", "MIOS_AI_SCRATCH_DIR",
    "MIOS_IMAGE_NAME", "MIOS_IMAGE_REF", "MIOS_IMAGE_TAG",
    "MIOS_BOOTSTRAP_MODE",
    "MIOS_SANDBOX_ENABLE", "MIOS_SECURITY_ALLOWLIST_HOSTS", "MIOS_SECURITY_PROBE_VERIFY_TLS",
    "MIOS_SECURITY_PROVENANCE_TAINT",
    "MIOS_SECURITY_SIGSTORE_COSIGN_VERSION", "MIOS_SECURITY_SIGSTORE_COSIGN_RELEASE_URL",
    "MIOS_HEADLESS", "MIOS_MONITOR_RUNNING", "MIOS_NO_COLOR", "MIOS_NO_MONITOR",
}

def emit_exports(data=None) -> dict[str, str]:
    """Emit all derived MIOS_* environment variables from SSOT layers (or from `data`, a merged table)."""
    import re as _re
    _re_unsafe = _re.compile(r"[^A-Za-z0-9_]")
    data = load_merged() if data is None else data
    ports = data.get("ports") or {}
    try:
        stack_offset = int(ports.get("stack_id", 0)) * 10000
    except (TypeError, ValueError):
        stack_offset = 0

    exports: dict[str, str] = {}
    legacy: dict[str, str] = {}
    for dotted, val in walk(data):
        sec_name = dotted.split(".")[0]
        if sec_name in EXCLUDED_SECTIONS:
            continue
        processed = process_val(dotted, val, stack_offset)
        if processed == "":
            continue
        canonical = canonical_name(dotted)
        if not (sec_name in WALK_MOSTLY_DEAD and canonical not in WALK_EMIT_KEEP):
            exports[canonical] = str(processed)
        for alias in get_aliases(dotted):
            # image.sidecars.* emits BOTH MIOS_<X>_IMAGE and MIOS_<X>_VERSION
            # from one key (get_aliases / aliases.rs), so without this split the
            # two names carry an identical value and the tree gains 23 new
            # duplicate-value groups -- exactly what value-dup-baseline.tsv says
            # to collapse rather than record. Dropping the split here was tried
            # and reverted for that reason.
            #
            # It is still the ONLY one of three emitters that splits, which is
            # task_3363's real finding: the fix is to stop emitting the redundant
            # _VERSION alias in both twins, not to change how it is rendered.
            # That removes 23 keys and needs its own change.
            if alias.endswith("_VERSION") and dotted.startswith("image.sidecars."):
                legacy[_re_unsafe.sub("_", alias)] = str(processed).rsplit(":", 1)[1] if ":" in str(processed) else "latest"
            else:
                legacy[_re_unsafe.sub("_", alias)] = str(processed)

    for name, value in legacy.items():
        exports.setdefault(name, value)

    for name, value in (colors(data) or {}).items():
        k = name.upper() if name.upper().startswith("MIOS_COLOR_") else "MIOS_COLOR_" + name.upper()
        exports.setdefault(_re_unsafe.sub("_", k), str(value))

    env_tbl = section(data, "env")
    if isinstance(env_tbl, dict):
        for k, v in sorted(env_tbl.items()):
            vp = process_val("env." + k, v, stack_offset)
            if vp is not None and vp != "":
                exports[_re_unsafe.sub("_", k)] = str(vp)

    resolve_cross_references(exports)
    return exports


def input_aliases(data: dict) -> dict[str, list[str]]:
    """Metadata-only compatibility boundary, matching native names::registry."""
    owners: dict[str, set[str]] = {}
    entries = []
    canonical_owners = {}
    exports = emit_exports(data)
    for key, _ in walk(data):
        canonical = canonical_name(key)
        if canonical in canonical_owners:
            raise ValueError(f"canonical variable collision {canonical}: {canonical_owners[canonical]} and {key}")
        canonical_owners[canonical] = key
        owners.setdefault(canonical, set()).add(key)
        aliases = [alias for alias in get_aliases(key) if alias != canonical]
        for alias in aliases:
            owners.setdefault(alias, set()).add(key)
        entries.append((key, canonical, aliases))
    return {
        canonical: [alias for alias in aliases if len(owners[alias]) == 1
                    and not (key.startswith("image.sidecars.") and alias.endswith("_VERSION"))]
        for key, canonical, aliases in entries if canonical in exports
    }


def overlay_inputs(data: dict, environment=os.environ.get) -> None:
    """Normalize process inputs without persisting values or revealing conflicts."""
    overrides = {}
    for canonical, aliases in input_aliases(data).items():
        value = environment(canonical)
        if not value:
            chosen = None
            for alias in aliases:
                candidate = environment(alias)
                if not candidate:
                    continue
                if chosen is not None and value != candidate:
                    raise ValueError(f"conflicting legacy inputs {chosen} and {alias}; set {canonical}")
                value, chosen = candidate, alias
        if value:
            overrides[canonical] = value
            overrides.update((alias, value) for alias in aliases)
    if overrides:
        env = data.setdefault("env", {})
        if not isinstance(env, dict):
            raise ValueError("SSOT env must be a table")
        env.update(overrides)


def resolve_cross_references(exports: dict[str, str]) -> None:
    """Resolve ${MIOS_*} that one emitted value makes to another.

    Twin of resolve_cross_references in tools/native/mios-resolver/src/emit.rs
    (Law 13), and public because both the resolver and the drift gate call it.

    Apply it where the consumer CANNOT expand: systemd EnvironmentFile= and
    podman --env-file read a value literally, so an emitted
    MIOS_AI_ENDPOINT=http://localhost:${MIOS_PORT_AGENT_PIPE}/v1 means one thing
    to bash and another to them. It is also why system-sync-env.sh DROPPED that
    variable rather than emitting it: its filter rejects any value containing
    `$` (task_997).

    Do NOT apply it to the export map that renders automation/lib/globals.{sh,ps1}.
    Those are sourced by bash and PowerShell, which expand at load time, and the
    live reference is the feature: exporting MIOS_PORT_AGENT_PIPE before sourcing
    propagates into MIOS_AI_ENDPOINT. render-globals.build_exports() therefore
    returns the unexpanded map.

    Reads a snapshot so the result does not depend on dict order, leaves `$$`
    alone because systemd owns it, and leaves an unresolvable name verbatim
    rather than blanking it so a caller can report it.
    """
    import re as _re2

    snapshot = dict(exports)
    _MAX_DEPTH = 16

    def _expand(text: str, depth: int = 0) -> str:
        if depth > _MAX_DEPTH or "${" not in text:
            return text
        out = []
        i = 0
        n = len(text)
        while i < n:
            if text.startswith("$$", i):
                out.append("$$")
                i += 2
                continue
            if text.startswith("${", i):
                close = _matching_brace(text, i)
                if close is not None:
                    inner = text[i + 2 : close]
                    name, default = _split_default(inner)
                    if _re2.fullmatch(r"MIOS_[A-Za-z0-9_]*", name):
                        val = snapshot.get(name)
                        if val:
                            out.append(_expand(val, depth + 1))
                        elif default is not None:
                            out.append(_expand(default, depth + 1))
                        else:
                            out.append(text[i : close + 1])
                        i = close + 1
                        continue
            out.append(text[i])
            i += 1
        return "".join(out)

    def _matching_brace(text: str, open_at: int):
        depth = 0
        i = open_at
        while i < len(text):
            if text.startswith("${", i):
                depth += 1
                i += 2
                continue
            if text[i] == "}":
                depth -= 1
                if depth == 0:
                    return i
            i += 1
        return None

    def _split_default(inner: str):
        depth = 0
        i = 0
        while i < len(inner):
            if inner.startswith("${", i):
                depth += 1
                i += 2
                continue
            if inner[i] == "}":
                depth = max(0, depth - 1)
                i += 1
                continue
            if depth == 0 and inner.startswith(":-", i):
                return inner[:i], inner[i + 2 :]
            i += 1
        return inner, None

    for key, value in list(exports.items()):
        if "${" in value:
            exports[key] = _expand(value)

if __name__ == "__main__":
    import json
    import shlex
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    data = load_merged()
    try:
        overlay_inputs(data)
    except ValueError as error:
        print(f"[mios-resolver] {error}", file=sys.stderr)
        raise SystemExit(1)
    exports = emit_exports(data)
    fmt = "shell"
    for arg in sys.argv[1:]:
        if arg.startswith("--emit="):
            fmt = arg.split("=", 1)[1]

    if fmt == "json":
        print(json.dumps(exports, indent=2))
    else:
        for k, v in sorted(exports.items()):
            print(f"export {k}={shlex.quote(str(v))}")
