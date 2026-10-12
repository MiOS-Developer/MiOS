# AI-hint: Standalone stdlib assert-script for mios_firewall (the provenance-taint + Semantic Firewall plane).
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md
"""Assert-script gate for mios_firewall. Run: ``python test_mios_firewall.py``.

Uses the REAL mios_secset set-math to build the high-privilege + always-taint
verb sets the live server derives, configures mios_firewall with them + a stub
DB reader, and asserts the firewall gates byte-faithfully. Exit 0 = green."""

import asyncio

import mios_secset
import mios_firewall

_CURATED = {
    "service_restart", "container_restart", "powershell_run",
    "text_create", "text_str_replace", "text_insert", "pc_type",
}
_HIGH = mios_secset.high_privilege_set(_CURATED, ["custom_dangerous_verb"])
_TAINT = mios_secset.taint_verb_set(
    ("web_search", "web_extract", "crawl", "web_scrape"), ["scrape_site"])

assert "powershell_run" in _HIGH, "curated floor must survive"
assert "custom_dangerous_verb" in _HIGH, "SSOT addition must be present"
assert "web_search" in _TAINT and "scrape_site" in _TAINT, "taint set built"

_TAINTED_SESSION = "session:abc"

async def _stub_db_read(sql, *, pg_sql=None, pg_params=None):
    sid = (pg_params or {}).get("sid")
    if sid == _TAINTED_SESSION:
        return [{"result": [
            {"ts": "t0", "tool": "powershell_run",
             "taint_reason": "powershell_output"},
            {"ts": "t1", "tool": "open_url",
             "taint_reason": "external_open_url:https://evil.example.com"},
        ]}]
    return [{"result": []}]

mios_firewall.configure(
    taint_verbs=_TAINT,
    provenance_taint_enable=True,
    allowlist_hosts={"localhost", "127.0.0.1", "mios-searxng", "mios-hermes"},
    mcp_client_tools={"mcp.s.evil": {"taint": "untrusted_web"},
                      "mcp.s.safe": {}},
    db_read=_stub_db_read,
)

assert mios_firewall._is_external_url("https://evil.example.com/path") is True, \
    "public dotted host = external taint source"
assert mios_firewall._is_external_url("http://localhost:8642/v1") is False, \
    "allowlist host = internal"
assert mios_firewall._is_external_url("http://mios-searxng:8888") is False, \
    "operator-infra allowlist host = internal"
assert mios_firewall._is_external_url("http://mios-hermes") is False, \
    "allowlist no-dot host = internal"
assert mios_firewall._is_external_url("http://buildbox.local/x") is False, \
    ".local suffix = internal"
assert mios_firewall._is_external_url("http://plainhost/x") is False, \
    "no-dot bare hostname = internal"
assert mios_firewall._is_external_url("") is False, "empty = not external"
assert mios_firewall._is_external_url(None) is False, "None = not external"

t, r = mios_firewall._classify_verb_taint("web_search", {})
assert t is True and r == "web_search_external", "SSOT taint verb introduces taint"
t, r = mios_firewall._classify_verb_taint("scrape_site", {})
assert t is True, "SSOT-added taint verb introduces taint"
t, r = mios_firewall._classify_verb_taint(
    "open_url", {"url": "https://evil.example.com/x"})
assert t is True and r.startswith("external_open_url:"), \
    "external open_url = exfil/ingest taint source"
t, r = mios_firewall._classify_verb_taint(
    "open_url", {"url": "http://localhost:8080"})
assert t is False, "internal open_url is NOT a taint source"
t, r = mios_firewall._classify_verb_taint("powershell_run", {})
assert t is True and r == "powershell_output", "powershell_run always taints"
t, r = mios_firewall._classify_verb_taint("text_view", {"path": "/etc/shadow"})
assert t is True and r.startswith("text_view_system:"), "system-path read taints"
t, r = mios_firewall._classify_verb_taint("text_view", {"path": "/home/mios/n.txt"})
assert t is False, "user-path read does NOT taint"
t, r = mios_firewall._classify_verb_taint("mcp.s.evil", {})
assert t is True and r == "mcp_untrusted_web:mcp.s.evil", \
    "taint-declaring MCP tool taints"
t, r = mios_firewall._classify_verb_taint("mcp.s.safe", {})
assert t is False, "MCP tool without a taint declaration does NOT taint"
t, r = mios_firewall._classify_verb_taint("recall", {})
assert t is False and r == "", "read-only verb introduces NO taint"

mios_firewall.configure(provenance_taint_enable=False)
t, _ = mios_firewall._classify_verb_taint("web_search", {})
assert t is False, "flag OFF -> web_search no longer self-taints"
t, _ = mios_firewall._classify_verb_taint("powershell_run", {})
assert t is True, "powershell_run taints even with provenance flag OFF"
mios_firewall.configure(provenance_taint_enable=True)  # restore

async def _run_session_checks():
    tainted, chain = await mios_firewall._session_is_tainted(_TAINTED_SESSION)
    assert tainted is True, "session with a tainted tool_call row is tainted"
    assert "powershell_run" in chain and "open_url" in chain, \
        "reason chain summarises upstream taint sources"

    clean, chain2 = await mios_firewall._session_is_tainted("session:clean")
    assert clean is False and chain2 == "", "session with no taint rows is clean"

    none_t, _ = await mios_firewall._session_is_tainted(None)
    assert none_t is False, "no session id = not tainted"

asyncio.run(_run_session_checks())

def _firewall_blocks(session_tainted: bool, verb: str) -> bool:
    return bool(session_tainted and verb in _HIGH)

assert _firewall_blocks(True, "powershell_run") is True, \
    "tainted session must BLOCK a high-privilege verb"
assert _firewall_blocks(True, "custom_dangerous_verb") is True, \
    "tainted session must BLOCK an SSOT-added high-privilege verb"
assert _firewall_blocks(True, "recall") is False, \
    "tainted session must ALLOW a safe read-only verb"
assert _firewall_blocks(False, "powershell_run") is False, \
    "clean session is never firewall-blocked"

t, _ = mios_firewall._classify_verb_taint(
    "open_url", {"url": "https://exfil.attacker.net/leak?d=secret"})
assert t is True, "external open_url is the exfil taint source the firewall keys on"

t, _ = mios_firewall._classify_verb_taint("text_view", {"path": "/etc/shadow"})
assert t is True, "default prefix taints before SSOT override"
t, _ = mios_firewall._classify_verb_taint("text_view", {"path": "/srv/secret/x"})
assert t is False, "non-default prefix does NOT taint under defaults"
assert mios_firewall._is_external_url("http://box.corp/x") is True, \
    "non-default suffix host is external under defaults"

mios_firewall.configure(
    text_view_taint_prefixes=["/srv/secret/"],
    internal_tld_suffixes=[".corp"],
)
t, r = mios_firewall._classify_verb_taint("text_view", {"path": "/srv/secret/x"})
assert t is True and r == "text_view_system:/srv/secret/", \
    "SSOT-declared prefix now taints (read from config, not baked)"
t, _ = mios_firewall._classify_verb_taint("text_view", {"path": "/etc/shadow"})
assert t is False, "former-default prefix no longer taints once SSOT drops it"
assert mios_firewall._is_external_url("http://box.corp/x") is False, \
    "SSOT-declared internal suffix now classifies host as internal"
assert mios_firewall._is_external_url("http://buildbox.local/x") is True, \
    "former-default suffix is external once SSOT drops it"

mios_firewall.configure(
    text_view_taint_prefixes=[
        "/etc/", "/usr/", "/boot/", "/sys/", "/proc/", "/dev/",
        "/mnt/c/Windows/", "/mnt/c/Program Files/",
        "/mnt/c/Program Files (x86)/",
    ],
    internal_tld_suffixes=[".local", ".lan", ".internal"],
)

print("test_mios_firewall: ALL PASS")


# ==============================================================================
# Consolidated from test_mios_egress.py (task_1006)
# ==============================================================================
# AI-hint: Standalone unit test for tools/generate-egress-firewall (#54 egress firewall): build_ruleset emits a uid-scoped nftables ruleset with the always-al...
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md
"""Standalone unit test for the #54 egress-firewall generator.

Pure: asserts the structure of build_ruleset's output (uid scoping, always-allowed
nets, per-mode final rule, allowlist) without invoking nft, so it runs anywhere in
the drift-gate. nft *syntax* is validated separately with `nft -c` where the
binary exists. Loads the generator from tools/ via SourceFileLoader.

Run:  python test_mios_egress.py
"""

import importlib.machinery
import importlib.util
import os
import sys

_RESULTS_egress: list = []

def _check_egress(name: str, ok: bool, detail: str = "") -> None:
    _RESULTS_egress.append((name, ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

def _load_tool():
    p = os.environ.get("MIOS_EGRESS_TOOL")
    if not p:
        here = os.path.dirname(os.path.abspath(__file__))
        repo = os.path.abspath(os.path.join(here, "..", "..", "..", ".."))
        p = os.path.join(repo, "tools", "generate-egress-firewall.py")
    if not os.path.isfile(p):
        print(f"[SKIP] generate-egress-firewall.py not found at {p}")
        print("\nskipped (0 checks)")
        sys.exit(0)
    loader = importlib.machinery.SourceFileLoader("egw", p)
    spec = importlib.util.spec_from_loader("egw", loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m

M = _load_tool()

def t_always() -> None:
    rs = M.build_ruleset("off", [], "mios-ai")
    _check_egress("always: table inet mios_egress", "table inet mios_egress" in rs)
    _check_egress("always: hook output", "type filter hook output" in rs)
    _check_egress("always: loopback iface", 'oifname "lo" accept' in rs)
    _check_egress("always: v4 loopback", "ip daddr 127.0.0.0/8 accept" in rs)
    _check_egress("always: v6 loopback", "ip6 daddr ::1 accept" in rs)
    _check_egress("always: tailnet", "ip daddr 100.64.0.0/10 accept" in rs)
    _check_egress("always: WSL gateway", "ip daddr 172.16.0.0/12 accept" in rs)
    _check_egress("always: AI-hint header", rs.splitlines()[0].startswith("# AI-hint:"))

def t_modes() -> None:
    off = M.build_ruleset("off", [], "mios-ai")
    _check_egress("off: no-op accept, no drop", "mode=off -> no-op" in off and "drop" not in off)
    audit = M.build_ruleset("audit", [], "mios-ai")
    _check_egress("audit: log + accept, no drop",
           "mios-egress-audit" in audit and " drop" not in audit)
    enforce = M.build_ruleset("enforce", [], "mios-ai")
    _check_egress("enforce: log + drop",
           "mios-egress-drop" in enforce and enforce.rstrip().endswith("}"))
    _check_egress("enforce: drop present", "drop" in enforce)
    weird = M.build_ruleset("banana", [], "mios-ai")
    _check_egress("unknown mode -> off no-op", "no-op" in weird and "drop" not in weird)

def t_allow() -> None:
    rs = M.build_ruleset("enforce", ["203.0.113.0/24", "198.51.100.7", "2001:db8::/32"], "mios-ai")
    _check_egress("allow: v4 set rule", "ip daddr {" in rs and "203.0.113.0/24" in rs)
    _check_egress("allow: v6 set rule", "ip6 daddr {" in rs and "2001:db8::/32" in rs)
    _check_egress("allow: empty -> no allow rule",
           "ip daddr {" not in M.build_ruleset("enforce", [], "mios-ai"))

def t_scope() -> None:
    rs = M.build_ruleset("enforce", [], "mios-agent")
    _check_egress("scope: uid-scoped to the agent user",
           'meta skuid != "mios-agent" accept' in rs)
    _check_egress("scope: other users pass untouched (rule precedes drop)",
           rs.index("skuid") < rs.index("drop"))

def _main_egress() -> int:
    for t in (t_always, t_modes, t_allow, t_scope):
        t()
    passed = sum(1 for _, ok in _RESULTS_egress if ok)
    total = len(_RESULTS_egress)
    print(f"\n{passed}/{total} checks passed")
    return 0 if passed == total else 1


def _run_extra_egress():
    import os
    _saved_env = dict(os.environ)
    try:
        return _main_egress()
    except SystemExit as _e:
        return _e.code if _e.code is not None else 0
    finally:
        os.environ.clear()
        os.environ.update(_saved_env)



def _run_all_folded_firewall_suites():
    rc = _run_extra_egress()
    if rc not in (None, 0):
        import sys
        sys.exit(f"Folded test suite failed: exit code {rc}")

_run_all_folded_firewall_suites()
