#!/usr/bin/env python3
# AI-hint: Standalone assert-script unit test for mios_sandbox (WS-A13 risk-tier dispatch sandbox). Pure stdlib, no server.py/bwrap/podman/pytest.
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md
"""Unit tests for mios_sandbox (WS-A13)."""

import sys

import mios_sandbox as sb

_fails = 0

def check(name, cond, detail=""):
    global _fails
    if not cond:
        _fails += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

def t_tiers():
    r = sb.resolve_profile("read")
    check("read: mechanism none", r.mechanism == "none" and r.confined is False)
    check("read: has network", r.network is True)
    w = sb.resolve_profile("write")
    check("write: workspace mechanism", w.mechanism == "workspace" and w.confined is True)
    check("write: needs workspace + ro root", w.workspace is True and w.read_only_root is True)
    i = sb.resolve_profile("interactive")
    check("interactive: strict", i.mechanism == "strict")
    check("interactive: NO network", i.network is False)

def t_fail_closed():
    for bad in ["", None, "supervisor", "weird", "READ-ish"]:
        p = sb.resolve_profile(bad)
        check(f"fail-closed: {bad!r} -> strict (never none)", p.mechanism == "strict" and p.confined is True, p.to_dict())

def t_explicit():
    check("explicit: 'none' override", sb.resolve_profile("interactive", explicit="none").mechanism == "none")
    check("explicit: 'strict' override", sb.resolve_profile("read", explicit="strict").mechanism == "strict")
    check("explicit: tier-name override", sb.resolve_profile("read", explicit="write").mechanism == "workspace")
    check("explicit: unknown override -> strict (fail-closed)",
          sb.resolve_profile("read", explicit="yolo").mechanism == "strict")

def t_workspace():
    p = sb.workspace_path("powershell_run", "abc123")
    check("workspace: under base", p.startswith("/var/lib/mios/ai/dispatch/"))
    check("workspace: verb hashed (not raw)", "powershell_run" not in p)
    check("workspace: deterministic per (verb,uniq)", p == sb.workspace_path("powershell_run", "abc123"))
    check("workspace: distinct verbs differ", sb.workspace_path("a", "u") != sb.workspace_path("b", "u"))
    check("workspace: sanitizes uniq (no traversal)", "/" not in sb.workspace_path("v", "../../etc").rsplit("/", 1)[1])

def t_shape():
    d = sb.resolve_profile("write").to_dict()
    check("shape: keys", set(d) == {"tier", "mechanism", "workspace", "read_only_root", "network", "confined"})

def t_bwrap():
    cmd = ["powershell_run", "--", "echo hi"]
    none_argv = sb.build_bwrap_argv(sb.resolve_profile("read"), cmd)
    check("bwrap: none tier runs direct (cmd unchanged)", none_argv == cmd)
    wp = sb.resolve_profile("write")
    wa = sb.build_bwrap_argv(wp, cmd, workspace="/var/lib/mios/ai/dispatch/x-1")
    check("bwrap: write starts with bwrap", wa[0] == "bwrap")
    check("bwrap: write has --unshare-all + --die-with-parent", "--unshare-all" in wa and "--die-with-parent" in wa)
    check("bwrap: write re-adds net (--share-net)", "--share-net" in wa)
    check("bwrap: write read-only root (--ro-bind / /)", "--ro-bind" in wa and wa[wa.index("--ro-bind")+1:wa.index("--ro-bind")+3] == ["/", "/"])
    check("bwrap: write binds the workspace rw", "--bind" in wa and "/var/lib/mios/ai/dispatch/x-1" in wa)
    check("bwrap: write proc/dev/tmpfs", "--proc" in wa and "--dev" in wa and "--tmpfs" in wa)
    check("bwrap: cmd after the -- separator", wa[wa.index("--", 1):] == ["--"] + cmd)
    sa = sb.build_bwrap_argv(sb.resolve_profile("interactive"), cmd, workspace="/ws")
    check("bwrap: strict has NO --share-net (no network)", "--share-net" not in sa)
    check("bwrap: strict still --unshare-all", "--unshare-all" in sa)
    uk = sb.build_bwrap_argv(sb.resolve_profile("bogus"), cmd, workspace="/ws")
    check("bwrap: unknown tier fail-closed (confined, no net)", uk[0] == "bwrap" and "--share-net" not in uk)

def t_sandbox_exec_prefix():
    check("prefix: none tier -> [] (no wrap)",
          sb.sandbox_exec_prefix(sb.resolve_profile("read")) == [])
    wp = sb.sandbox_exec_prefix(sb.resolve_profile("write"), workspace="/ws/x-1")
    check("prefix: write level enforce", wp[:3] == ["mios-sandbox-exec", "--level", "enforce"])
    check("prefix: write keeps net (--net)", "--net" in wp)
    check("prefix: write binds workspace", "--workspace" in wp and "/ws/x-1" in wp)
    check("prefix: write ends in -- separator", wp[-1] == "--")
    sp = sb.sandbox_exec_prefix(sb.resolve_profile("interactive"), workspace="/ws")
    check("prefix: strict no --net (no egress)", "--net" not in sp and sp[-1] == "--")
    uk = sb.sandbox_exec_prefix(sb.resolve_profile("bogus"), workspace="/ws")
    check("prefix: unknown tier fail-closed (confined, no net)",
          uk and uk[0] == "mios-sandbox-exec" and "--net" not in uk)

def main():
    t_tiers()
    t_fail_closed()
    t_explicit()
    t_workspace()
    t_shape()
    t_bwrap()
    t_sandbox_exec_prefix()
    print(f"\n{'ok' if _fails == 0 else str(_fails) + ' FAILED'}")
    return 1 if _fails else 0



# ==============================================================================
# Consolidated from test_mios_mcp_sandbox.py (task_1006)
# ==============================================================================
# AI-hint: Standalone assert-script unit test for task_32 (SEC-01 Hermetic MCP Sandboxing). Pure stdlib + asyncio, no server.py/DB/network.
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md
"""Unit tests for task_32 SEC-01 Hermetic MCP Sandboxing."""

import asyncio
import os
import sys
import json
import subprocess
import tempfile
import stat

_fails_mcp_sandbox = 0

def _gatekeeper_path():
    """Resolve the MCP gatekeeper script: the installed path in-image, else the
    source-tree copy. The CI drift-gate runs this test from a source checkout
    BEFORE any install, so /usr/libexec is not populated; fall back to the repo's
    usr/libexec/mios/mcp-server-runner (three levels up from the agent-pipe dir)."""
    p = "/usr/libexec/mios/mcp-server-runner"
    if os.path.isfile(p):
        return p
    src = os.path.normpath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "..", "..", "libexec", "mios", "mcp-server-runner"))
    return src if os.path.isfile(src) else p

def _check_mcp_sandbox(name, cond, detail=""):
    global _fails_mcp_sandbox
    if not cond:
        _fails_mcp_sandbox += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

def _repo_file(*parts):
    """Source-tree path relative to the repo root (agent-pipe is usr/lib/mios/agent-pipe)."""
    return os.path.normpath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..", *parts))

def _runner_preamble(paths_sh):
    """The runner's preamble (shebang through the export line) with the
    installed paths.sh path pointed at a fixture, so the port contract runs in
    isolation -- never against the live installation."""
    src = _repo_file("usr", "libexec", "mios", "mcp-server-runner")
    with open(src) as f:
        lines = f.read().splitlines()
    end = next(i for i, l in enumerate(lines) if l.startswith("export MIOS_AI_ENDPOINT"))
    body = "\n".join(lines[:end + 1]).replace('"/usr/lib/mios/paths.sh"', f'"{paths_sh}"')
    return body + '\necho "PORT=$MIOS_PORTS_MCP"\n'

def _run_preamble(paths_sh, env):
    with tempfile.TemporaryDirectory() as d:
        # Put the copy where the ../../lib/mios/paths.sh fallback does not exist.
        script = os.path.join(d, "a", "b", "runner")
        os.makedirs(os.path.dirname(script))
        with open(script, "w") as f:
            f.write(_runner_preamble(paths_sh))
        return subprocess.run(["bash", script], capture_output=True, text=True,
                              timeout=10, env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), **env})

def t_runner_port_contract():
    """task_3428: the port comes only from the resolver-rendered install.env --
    no Environment= literal in the unit or its mios.toml table -- and an
    unresolved port (or a missing paths.sh) is a named, non-zero failure."""
    if sys.platform == "win32":
        print("[SKIP] runner-port: bash evaluation skipped on Windows host")
        return
    import re
    lit = re.compile(r"MIOS_(?:PORTS?_MCP|MCP_PORT)=")
    with open(_repo_file("usr", "lib", "systemd", "system", "mios-mcp.service")) as f:
        unit_env = [l for l in f.read().splitlines()
                    if l.startswith("Environment=") and lit.search(l)]
    _check_mcp_sandbox("runner-port: unit has no MCP port Environment= literal",
                       not unit_env, f"got {unit_env}")
    try:
        import tomllib
    except ImportError:
        tomllib = None
    _check_mcp_sandbox("runner-port: tomllib available (python>=3.11)", tomllib is not None)
    if tomllib is not None:
        with open(_repo_file("usr", "share", "mios", "mios.toml"), "rb") as f:
            svc = tomllib.load(f)["units"]["mios-mcp.service"]["Service"]
        envs = svc.get("Environment", [])
        envs = [envs] if isinstance(envs, str) else list(envs)
        bad = [e for e in envs if lit.search(e)]
        _check_mcp_sandbox("runner-port: mios.toml unit table has no MCP port literal",
                           not bad, f"got {bad}")
        _check_mcp_sandbox("runner-port: unit reads the resolver-rendered install.env",
                           svc.get("EnvironmentFile") == "-/etc/mios/install.env",
                           f"got {svc.get('EnvironmentFile')!r}")

    with tempfile.TemporaryDirectory() as d:
        stub = os.path.join(d, "paths.sh")
        with open(stub, "w") as f:
            f.write(":\n")
        ep = {"MIOS_AI_ENDPOINT": "http://localhost:1/v1"}
        for name, extra in (("MIOS_PORTS_MCP only", {"MIOS_PORTS_MCP": "8770"}),
                            ("MIOS_PORTS_MCP only", {"MIOS_PORTS_MCP": "8770"})):
            r = _run_preamble(stub, {**ep, **extra})
            _check_mcp_sandbox(f"runner-port: {name} resolves",
                               r.returncode == 0 and "PORT=8770" in r.stdout,
                               f"rc={r.returncode} out={r.stdout.strip()!r} err={r.stderr.strip()[-160:]!r}")
        r = _run_preamble(stub, ep)
        _check_mcp_sandbox("runner-port: unset port fails with named error",
                           r.returncode != 0 and "MIOS_PORTS_MCP is unset" in r.stderr
                           and "PORT=" not in r.stdout,
                           f"rc={r.returncode} err={r.stderr.strip()[-160:]!r}")
        r = _run_preamble(os.path.join(d, "absent", "paths.sh"),
                          {**ep, "MIOS_PORTS_MCP": "8770"})
        _check_mcp_sandbox("runner-port: missing paths.sh is a hard failure",
                           r.returncode != 0 and "paths.sh not found" in r.stderr
                           and "PORT=" not in r.stdout,
                           f"rc={r.returncode} err={r.stderr.strip()[-160:]!r}")

def t_sandbox_gate_parsing():
    """Verify that [security.mcp_sandbox].enable is read correctly from config."""
    from mios_config import _toml_section

    sec_cfg = (_toml_section("security") or {}).get("mcp_sandbox") or {}
    if isinstance(sec_cfg, str):
        sec_cfg = {}
    enable_val = sec_cfg.get("enable", "false")
    _check_mcp_sandbox("sandbox-gate: default enable is false-ish",
          str(enable_val).strip().lower() in {"false", "0", "no", "off", ""},
          f"got {enable_val!r}")

    wap = sec_cfg.get("write_allowed_paths", [])
    _check_mcp_sandbox("sandbox-gate: write_allowed_paths is a list", isinstance(wap, list),
          f"got {type(wap)}")

    import mios_pipe.federation.mcp as mc
    _check_mcp_sandbox("sandbox-gate: MCP_SANDBOX_ENABLE is False by default",
          mc.MCP_SANDBOX_ENABLE is False, f"got {mc.MCP_SANDBOX_ENABLE}")
    _check_mcp_sandbox("sandbox-gate: MCP_SANDBOX_GATEKEEPER path set",
          mc.MCP_SANDBOX_GATEKEEPER == "/usr/libexec/mios/mcp-server-runner")

def t_gatekeeper_traversal_blocking():
    """Verify the gatekeeper script blocks directory traversal patterns."""
    gatekeeper = _gatekeeper_path()
    if not os.path.isfile(gatekeeper):
        _check_mcp_sandbox("gatekeeper-traversal: gatekeeper exists", False, "file not found")
        return
    if sys.platform == "win32":
        print("[SKIP] gatekeeper-traversal: bash subshell evaluation skipped on Windows host")
        return
    posix_gatekeeper = gatekeeper.replace('\\', '/')
    if len(posix_gatekeeper) >= 2 and posix_gatekeeper[1] == ':':
        posix_gatekeeper = '/' + posix_gatekeeper[0].lower() + posix_gatekeeper[2:]

    result = subprocess.run(
        ["bash", "-c", f"""
            source /usr/lib/mios/paths.sh 2>/dev/null || true
            _log() {{ true; }}
            eval "$(sed -n '/_block_traversal()/,/^}}/p' '{posix_gatekeeper}')"
            _block_traversal "../../etc/passwd"
            echo $?
        """],
        capture_output=True, text=True, timeout=5
    )
    exit_code = result.stdout.strip().split('\n')[-1] if result.stdout.strip() else ""
    _check_mcp_sandbox("gatekeeper-traversal: ../../etc/passwd blocked",
          exit_code == "1", f"exit={exit_code} stdout={result.stdout[:100]}")

    result2 = subprocess.run(
        ["bash", "-c", f"""
            source /usr/lib/mios/paths.sh 2>/dev/null || true
            _log() {{ true; }}
            eval "$(sed -n '/_block_traversal()/,/^}}/p' '{posix_gatekeeper}')"
            _block_traversal "/var/lib/mios/ai/data.json"
            echo $?
        """],
        capture_output=True, text=True, timeout=5
    )
    exit_code2 = result2.stdout.strip().split('\n')[-1] if result2.stdout.strip() else ""
    _check_mcp_sandbox("gatekeeper-traversal: /var/lib/mios/ai/data.json allowed",
          exit_code2 == "0", f"exit={exit_code2}")

    result3 = subprocess.run(
        ["bash", "-c", f"""
            source /usr/lib/mios/paths.sh 2>/dev/null || true
            _log() {{ true; }}
            eval "$(sed -n '/_block_traversal()/,/^}}/p' '{posix_gatekeeper}')"
            _block_traversal "/etc/shadow"
            echo $?
        """],
        capture_output=True, text=True, timeout=5
    )
    exit_code3 = result3.stdout.strip().split('\n')[-1] if result3.stdout.strip() else ""
    _check_mcp_sandbox("gatekeeper-traversal: /etc/shadow blocked",
          exit_code3 == "1", f"exit={exit_code3}")

def t_gatekeeper_write_path_validation():
    """Verify the gatekeeper's _validate_write_path function."""
    gatekeeper = _gatekeeper_path()
    if not os.path.isfile(gatekeeper):
        _check_mcp_sandbox("gatekeeper-write-path: gatekeeper exists", False, "file not found")
        return
    if sys.platform == "win32":
        print("[SKIP] gatekeeper-write-path: bash subshell evaluation skipped on Windows host")
        return

    result = subprocess.run(
        ["bash", "-c", f"""
            export MIOS_WRITE_ALLOWED_PATHS="/tmp/mios-mcp:/var/lib/mios/ai"
            source /usr/lib/mios/paths.sh 2>/dev/null || true
            _log() {{ true; }}
            eval "$(sed -n '/_validate_write_path()/,/^}}/p' '{gatekeeper}')"
            _validate_write_path "/tmp/mios-mcp/foo.txt"
            echo $?
        """],
        capture_output=True, text=True, timeout=5
    )
    exit_code = result.stdout.strip().split('\n')[-1] if result.stdout.strip() else ""
    _check_mcp_sandbox("gatekeeper-write-path: /tmp/mios-mcp/foo.txt allowed",
          exit_code == "0", f"exit={exit_code}")

    result2 = subprocess.run(
        ["bash", "-c", f"""
            export MIOS_WRITE_ALLOWED_PATHS="/tmp/mios-mcp:/var/lib/mios/ai"
            source /usr/lib/mios/paths.sh 2>/dev/null || true
            _log() {{ true; }}
            eval "$(sed -n '/_validate_write_path()/,/^}}/p' '{gatekeeper}')"
            _validate_write_path "/etc/passwd"
            echo $?
        """],
        capture_output=True, text=True, timeout=5
    )
    exit_code2 = result2.stdout.strip().split('\n')[-1] if result2.stdout.strip() else ""
    _check_mcp_sandbox("gatekeeper-write-path: /etc/passwd blocked",
          exit_code2 == "1", f"exit={exit_code2}")

def t_spawn_routes_through_gatekeeper():
    """Verify _McpStdioClient._spawn prepends gatekeeper when sandbox is enabled."""
    import mios_pipe.federation.mcp as mc

    orig_enable = mc.MCP_SANDBOX_ENABLE
    orig_gatekeeper = mc.MCP_SANDBOX_GATEKEEPER

    with tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False) as f:
        f.write('#!/bin/bash\necho "gatekeeper: $@"\nexec "$@"\n')
        fake_gk = f.name
    os.chmod(fake_gk, stat.S_IRWXU)

    try:
        mc.MCP_SANDBOX_ENABLE = True
        mc.MCP_SANDBOX_GATEKEEPER = fake_gk

        client = mc._McpStdioClient("test-sid", "/usr/bin/echo", ["hello"], {}, None)

        _cmd = client.command
        _args = list(client.args)
        if mc.MCP_SANDBOX_ENABLE and os.path.isfile(mc.MCP_SANDBOX_GATEKEEPER):
            _args = [_cmd] + _args
            _cmd = mc.MCP_SANDBOX_GATEKEEPER

        _check_mcp_sandbox("spawn-gatekeeper: command is gatekeeper",
              _cmd == fake_gk, f"got {_cmd}")
        _check_mcp_sandbox("spawn-gatekeeper: original command is first arg",
              _args[0] == "/usr/bin/echo", f"got {_args}")
        _check_mcp_sandbox("spawn-gatekeeper: original args preserved",
              _args[1] == "hello", f"got {_args}")
    finally:
        mc.MCP_SANDBOX_ENABLE = orig_enable
        mc.MCP_SANDBOX_GATEKEEPER = orig_gatekeeper
        os.unlink(fake_gk)

def t_spawn_direct_when_disabled():
    """Verify _McpStdioClient._spawn runs command directly when sandbox is disabled."""
    import mios_pipe.federation.mcp as mc

    orig_enable = mc.MCP_SANDBOX_ENABLE

    try:
        mc.MCP_SANDBOX_ENABLE = False

        client = mc._McpStdioClient("test-sid", "/usr/bin/echo", ["hello"], {}, None)

        _cmd = client.command
        _args = list(client.args)
        if mc.MCP_SANDBOX_ENABLE and os.path.isfile(mc.MCP_SANDBOX_GATEKEEPER):
            _args = [_cmd] + _args
            _cmd = mc.MCP_SANDBOX_GATEKEEPER

        _check_mcp_sandbox("spawn-direct: command is original (not gatekeeper)",
              _cmd == "/usr/bin/echo", f"got {_cmd}")
        _check_mcp_sandbox("spawn-direct: args are original",
              _args == ["hello"], f"got {_args}")
    finally:
        mc.MCP_SANDBOX_ENABLE = orig_enable

def t_fapolicyd_rules_structure():
    """Verify fapolicyd.rules has MCP execution carve-outs and still ends with deny_audit."""
    rules_path = "/etc/fapolicyd/fapolicyd.rules"
    if not os.path.isfile(rules_path):
        for candidate in [
            "/mnt/c/MiOS/etc/fapolicyd/fapolicyd.rules",
            os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "..", "..", "..", "..", "etc", "fapolicyd", "fapolicyd.rules"),
        ]:
            if os.path.isfile(candidate):
                rules_path = candidate
                break
        else:
            _check_mcp_sandbox("fapolicyd-rules: file exists", False, "not found at any candidate path")
            return

    with open(rules_path) as f:
        content = f.read()

    _check_mcp_sandbox("fapolicyd-rules: contains /var/lib/mios/ai/ allow rule",
          "dir=/var/lib/mios/ai/" in content)
    _check_mcp_sandbox("fapolicyd-rules: contains /srv/ai/mcp/ allow rule",
          "dir=/srv/ai/mcp/" in content)

    lines = [l.strip() for l in content.strip().splitlines()
             if l.strip() and not l.strip().startswith("#")]
    _check_mcp_sandbox("fapolicyd-rules: last rule is deny_audit",
          lines[-1].startswith("deny_audit"), f"got: {lines[-1]}")

def _main_mcp_sandbox():
    print("=== Running task_32 Hermetic MCP Sandboxing Tests ===")
    t_runner_port_contract()
    t_sandbox_gate_parsing()
    t_gatekeeper_traversal_blocking()
    t_gatekeeper_write_path_validation()
    t_spawn_routes_through_gatekeeper()
    t_spawn_direct_when_disabled()
    t_fapolicyd_rules_structure()
    print(f"=== task_32 Hermetic MCP Sandboxing Tests Done ({_fails_mcp_sandbox} failures) ===")
    sys.exit(1 if _fails_mcp_sandbox > 0 else 0)


def _run_extra_mcp_sandbox():
    import os
    _saved_env = dict(os.environ)
    try:
        return _main_mcp_sandbox()
    except SystemExit as _e:
        return _e.code if _e.code is not None else 0
    finally:
        os.environ.clear()
        os.environ.update(_saved_env)



# ==============================================================================
# Consolidated from test_mios_seccomp.py (task_1006)
# ==============================================================================
# AI-hint: Standalone assert-script unit test for mios_pipe.access.seccomp (task_210).
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md

"""Unit tests for the dispatch sandbox's seccomp filter (task_210)."""

import glob
import re
import struct
import sys

from mios_pipe.access import seccomp as S

_fails_seccomp = 0

def _check_seccomp(name, cond, detail=""):
    global _fails_seccomp
    if not cond:
        _fails_seccomp += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

def _insns(blob):
    return [struct.unpack("<HBBI", blob[i:i + 8]) for i in range(0, len(blob), 8)]

def t_table_matches_kernel_abi():
    """A wrong number denies the wrong syscall, so the table is checked against
    the kernel's own header rather than trusted."""
    hdrs = glob.glob("/usr/include/**/asm/unistd_64.h", recursive=True)
    if not hdrs:
        print("[SKIP] ABI cross-check: no asm/unistd_64.h on this host")
        return
    text = open(hdrs[0], encoding="utf-8", errors="replace").read()
    kernel = {m.group(1): int(m.group(2))
              for m in re.finditer(r"^#define __NR_(\w+)\s+(\d+)", text, re.M)}
    _check_seccomp("ABI: the header parsed", len(kernel) > 200, str(len(kernel)))
    bad = [(n, v, kernel.get(n)) for n, v in S.SYSCALLS["x86_64"].items()
           if n in kernel and kernel[n] != v]
    _check_seccomp("ABI: every committed x86_64 number matches the kernel header",
          not bad, str(bad))
    missing = [n for n in S.SYSCALLS["x86_64"] if n not in kernel]
    _check_seccomp("ABI: no committed name is unknown to the kernel header",
          not missing, str(missing))

def t_denylist():
    base = S.resolve_denylist("x86_64")
    _check_seccomp("deny: the baseline floor resolves", len(base) >= 15, str(len(base)))
    ext = S.resolve_denylist("x86_64", ["swapon", "reboot"])
    _check_seccomp("deny: an SSOT list EXTENDS the floor", len(ext) > len(base))
    _check_seccomp("deny: the floor survives the extension", set(base) <= set(ext))
    _check_seccomp("deny: an unknown name is dropped, never guessed",
          S.resolve_denylist("x86_64", ["not_a_syscall"]) == base)
    _check_seccomp("deny: the result is sorted and unique",
          ext == sorted(set(ext)))
    tbl = S.syscall_numbers("x86_64")
    _check_seccomp("deny: ptrace is on the floor", tbl["ptrace"] in base)
    _check_seccomp("deny: mount is on the floor", tbl["mount"] in base)

def t_program_shape():
    nrs = S.resolve_denylist("x86_64")
    blob = S.build_filter("x86_64", nrs)
    ins = _insns(blob)
    _check_seccomp("prog: length is a whole number of sock_filters", len(blob) % 8 == 0)
    _check_seccomp("prog: it loads the ARCH first", ins[0] == (0x20, 0, 0, 4), str(ins[0]))
    _check_seccomp("prog: the arch compare names x86_64",
          ins[1][0] == 0x15 and ins[1][3] == S.AUDIT_ARCH["x86_64"], str(ins[1]))

    # The classic bypass: an arch mismatch that ALLOWS. It must land on DENY.
    n = len(nrs)
    _check_seccomp("prog: an arch MISMATCH falls through to the deny tail, not allow",
          ins[1][2] == n + 1, f"jf={ins[1][2]} n={n}")
    _check_seccomp("prog: then it loads the syscall number", ins[2] == (0x20, 0, 0, 0))
    _check_seccomp("prog: one compare per denied syscall",
          len(ins) == 3 + n + 2, f"{len(ins)} vs {3 + n + 2}")
    _check_seccomp("prog: the allow return is second to last", ins[-2] == (0x06, 0, 0, S.RET_ALLOW))
    _check_seccomp("prog: the tail returns EPERM by default",
          ins[-1] == (0x06, 0, 0, 0x00050000 | 1), str(ins[-1]))
    _check_seccomp("prog: every compare jumps to the deny tail",
          all(ins[3 + i][1] == n - i for i in range(n)))

    killed = _insns(S.build_filter("x86_64", nrs, action="kill"))
    _check_seccomp("prog: action=kill returns KILL_PROCESS",
          killed[-1] == (0x06, 0, 0, S.RET_KILL_PROCESS), str(killed[-1]))
    _check_seccomp("prog: an unknown action falls back to errno, never to allow",
          _insns(S.build_filter("x86_64", nrs, action="bogus"))[-1][3] != S.RET_ALLOW)

def t_refusals():
    for name, fn in (
        ("an unsupported architecture", lambda: S.build_filter("riscv64", [1])),
        ("a filter that denies nothing", lambda: S.build_filter("x86_64", [])),
        ("more denied syscalls than the jump range", lambda: S.build_filter(
            "x86_64", list(range(300)))),
        ("a denylist for an unknown arch", lambda: S.resolve_denylist("riscv64")),
        ("audit_arch for an unknown arch", lambda: S.audit_arch("riscv64")),
    ):
        try:
            fn()
            _check_seccomp(f"refuse: {name}", False, "did not raise")
        except S.SeccompUnsupported:
            _check_seccomp(f"refuse: {name}", True)

def _main_seccomp():
    t_table_matches_kernel_abi()
    t_denylist()
    t_program_shape()
    t_refusals()
    print(f"\n{'ok' if _fails_seccomp == 0 else str(_fails_seccomp) + ' FAILED'}")
    return 1 if _fails_seccomp else 0


def _run_extra_seccomp():
    import os
    _saved_env = dict(os.environ)
    try:
        return _main_seccomp()
    except SystemExit as _e:
        return _e.code if _e.code is not None else 0
    finally:
        os.environ.clear()
        os.environ.update(_saved_env)



def _run_all_folded_sandbox_suites():
    rc = _run_extra_mcp_sandbox()
    if rc not in (None, 0):
        import sys
        sys.exit(f"Folded test suite failed: exit code {rc}")
    rc = _run_extra_seccomp()
    if rc not in (None, 0):
        import sys
        sys.exit(f"Folded test suite failed: exit code {rc}")

if __name__ == "__main__":
    _rc_main = main()
    _run_all_folded_sandbox_suites()
    sys.exit(_rc_main)
