#!/usr/bin/env python3
# AI-hint: Standalone assert-script unit test for mios_crl (WS-A10 cert/token revocation list). Pure stdlib, no server.py/DB/pytest/network.
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md
"""Unit tests for mios_crl (WS-A10 token/cert revocation list)."""

import sys

import mios_crl as crl

_fails = 0

def check(name, cond, detail=""):
    global _fails
    if not cond:
        _fails += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

def t_empty_default():
    c = crl.CRL()
    check("empty: len 0", len(c) == 0)
    check("empty: ids() == []", c.ids() == [])
    check("empty: unknown id not revoked", c.is_revoked("tok-xyz") is False)
    check("empty: None not revoked", c.is_revoked(None) is False)
    check("empty: blank not revoked", c.is_revoked("") is False)

def t_revoke_is_revoked():
    c = crl.CRL()
    check("revoke: not revoked before", c.is_revoked("t1") is False)
    c.revoke("t1")
    check("revoke: revoked after", c.is_revoked("t1") is True)
    check("revoke: len 1", len(c) == 1)
    check("revoke: ids has t1", c.ids() == ["t1"])
    check("revoke: unrelated id still unrevoked", c.is_revoked("t2") is False)
    c.revoke("t1")
    check("revoke: idempotent (no dup)", len(c) == 1)

def t_restore():
    c = crl.CRL(["a", "b", "c"])
    check("restore: a revoked before", c.is_revoked("a") is True)
    c.restore("a")
    check("restore: a not revoked after", c.is_revoked("a") is False)
    check("restore: others untouched", c.is_revoked("b") and c.is_revoked("c"))
    check("restore: len drops to 2", len(c) == 2)
    check("restore: ids drops a", c.ids() == ["b", "c"])
    c.restore("never-was-here")
    check("restore: unknown id no-op (no raise, len stable)", len(c) == 2)
    c.revoke("a")
    check("restore: re-revoke works", c.is_revoked("a") is True)

def t_ids_sorted_and_current():
    c = crl.CRL()
    c.revoke("zeta")
    c.revoke("alpha")
    c.revoke("mike")
    check("ids: sorted", c.ids() == ["alpha", "mike", "zeta"])
    c.restore("mike")
    check("ids: reflects restore", c.ids() == ["alpha", "zeta"])
    got = c.ids()
    got.append("INJECTED")
    check("ids: returned list is a copy (no leak back)",
          c.is_revoked("INJECTED") is False and c.ids() == ["alpha", "zeta"])

def t_init_normalization():
    c = crl.CRL(["  pad  ", "dup", "dup", "", "   ", "x"])
    check("init: strips whitespace", c.is_revoked("pad") is True)
    check("init: padded lookup also strips", c.is_revoked("  pad  ") is True)
    check("init: dedups", c.ids().count("dup") == 1)
    check("init: drops empty + blank", "" not in c.ids() and len(c) == 3,
          detail=str(c.ids()))
    check("init: ids == sorted(dedup,strip)", c.ids() == ["dup", "pad", "x"])
    c2 = crl.CRL([123, 456])
    check("init: int coerced to str", c2.is_revoked("123") is True and c2.is_revoked(123) is True)
    check("init: None source -> empty", len(crl.CRL(None)) == 0)

def t_revoke_normalization():
    c = crl.CRL()
    c.revoke("  spaced  ")
    check("revoke: stores stripped", c.ids() == ["spaced"])
    check("revoke: padded lookup hits", c.is_revoked("spaced") is True)
    c.revoke("")
    c.revoke("   ")
    c.revoke(None)
    check("revoke: blank/None ignored", c.ids() == ["spaced"] and "" not in c.ids())
    c.revoke(999)
    check("revoke: int coerced", c.is_revoked("999") is True)

def t_load_list():
    c = crl.CRL.load(["t1", "t2"])
    check("load list: type CRL", isinstance(c, crl.CRL))
    check("load list: round-trips ids", c.ids() == ["t1", "t2"])
    check("load list: revoked", c.is_revoked("t1") and c.is_revoked("t2"))
    check("load tuple: works", crl.CRL.load(("a", "b")).ids() == ["a", "b"])
    check("load set: works", crl.CRL.load({"a", "b"}).ids() == ["a", "b"])

def t_load_dict():
    src = {"revoked": ["compromised-1", "retired-peer"], "issued": ["live-1"]}
    c = crl.CRL.load(src)
    check("load dict: pulls revoked[]", c.ids() == ["compromised-1", "retired-peer"])
    check("load dict: revoked id is revoked", c.is_revoked("compromised-1") is True)
    check("load dict: ignores non-revoked keys (issued not revoked)",
          c.is_revoked("live-1") is False)
    check("load dict: missing revoked -> empty", len(crl.CRL.load({"issued": ["x"]})) == 0)
    check("load dict: revoked=None -> empty", len(crl.CRL.load({"revoked": None})) == 0)
    check("load dict: revoked=[] -> empty", len(crl.CRL.load({"revoked": []})) == 0)

def t_load_malformed_degrades_open():
    for bad in (None, "a-bare-string", 42, 3.14, object()):
        c = crl.CRL.load(bad)
        check(f"load malformed degrades open: {type(bad).__name__}",
              isinstance(c, crl.CRL) and len(c) == 0)
    c = crl.CRL.load("abc")
    check("load malformed: bare string NOT char-exploded", len(c) == 0 and c.is_revoked("a") is False)

def t_merge_unions():
    c = crl.CRL(["a", "b"])
    c.merge(["b", "c", "d"])
    check("merge: unions (no dup on b)", c.ids() == ["a", "b", "c", "d"])
    check("merge: new ids revoked", c.is_revoked("c") and c.is_revoked("d"))
    check("merge: original ids retained", c.is_revoked("a") and c.is_revoked("b"))
    c.merge(["  e  ", "", "   ", None])
    check("merge: normalizes ids (strip + drop blank/None)",
          c.is_revoked("e") is True and "" not in c.ids())
    before = c.ids()
    c.merge(None)
    c.merge([])
    check("merge: None/empty no-op", c.ids() == before)

def t_merge_two_crls():
    a = crl.CRL(["x", "y"])
    b = crl.CRL(["y", "z"])
    a.merge(b.ids())
    check("merge two CRLs: union of both", a.ids() == ["x", "y", "z"])
    check("merge two CRLs: source CRL unchanged", b.ids() == ["y", "z"])

def t_no_shared_state_between_instances():
    a = crl.CRL(["shared"])
    b = crl.CRL(["shared"])
    a.revoke("a-only")
    check("isolation: b has no a-only", b.is_revoked("a-only") is False)
    b.restore("shared")
    check("isolation: a still has shared after b restores it", a.is_revoked("shared") is True)

def main():
    t_empty_default()
    t_revoke_is_revoked()
    t_restore()
    t_ids_sorted_and_current()
    t_init_normalization()
    t_revoke_normalization()
    t_load_list()
    t_load_dict()
    t_load_malformed_degrades_open()
    t_merge_unions()
    t_merge_two_crls()
    t_no_shared_state_between_instances()
    print(f"\n{'ok' if _fails == 0 else str(_fails) + ' FAILED'}")
    return 1 if _fails else 0



# ==============================================================================
# Consolidated from test_mios_mtls.py (task_1006)
# ==============================================================================
# AI-hint: Standalone unit test for tools/provision-agent-mtls (#54 mTLS PKI): the agent leaf cert is signed by the CA, carries clientAuth+serverAuth EKU, and r...
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md
"""Standalone unit test for the #54 mTLS provisioning tool.

Provisions into a temp dir and verifies the PKI is correct: the agent leaf is
signed by the CA, it is valid for BOTH client + server auth, and re-running keeps
the existing CA (so exchanged peer trust is not invalidated). Needs cryptography;
SKIPS (exit 0) if it is unavailable so the drift-gate stays portable.

Run:  python test_mios_mtls.py
"""

import importlib.machinery
import importlib.util
import os
import sys
import tempfile

_RESULTS_mtls: list = []

def _check_mtls(name: str, ok: bool, detail: str = "") -> None:
    _RESULTS_mtls.append((name, ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

def _skip(msg: str) -> None:
    print(f"[SKIP] {msg}")
    print("\nskipped (0 checks)")
    sys.exit(0)

try:
    from cryptography import x509
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID
except ModuleNotFoundError:
    _skip("cryptography not installed")

def _load_tool():
    p = os.environ.get("MIOS_MTLS_TOOL")
    if not p:
        here = os.path.dirname(os.path.abspath(__file__))
        repo = os.path.abspath(os.path.join(here, "..", "..", "..", ".."))
        p = os.path.join(repo, "tools", "provision-agent-mtls.py")
    if not os.path.isfile(p):
        _skip(f"provision-agent-mtls.py not found at {p}")
    loader = importlib.machinery.SourceFileLoader("provmtls", p)
    spec = importlib.util.spec_from_loader("provmtls", loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m

def _provision(tool, d):
    os.environ["MIOS_MTLS_DIR"] = d
    os.environ["MIOS_MTLS_CN"] = "test-agent"
    os.environ["MIOS_TOML"] = os.path.join(d, "no-such.toml")  # force defaults
    cfg = tool._cfg()
    ca_cert, ca_key, minted = tool.ensure_ca(cfg)
    tool.issue_agent_cert(cfg, ca_cert, ca_key)
    return cfg, minted

def _main_mtls() -> int:
    tool = _load_tool()
    with tempfile.TemporaryDirectory() as d:
        cfg, minted = _provision(tool, d)
        _check_mtls("ca: minted on first run", minted is True)
        ca = x509.load_pem_x509_certificate(open(cfg["ca_cert"], "rb").read())
        agent = x509.load_pem_x509_certificate(open(cfg["cert"], "rb").read())

        _check_mtls("chain: issuer == CA subject", agent.issuer == ca.subject)
        try:
            ca.public_key().verify(
                agent.signature, agent.tbs_certificate_bytes,
                ec.ECDSA(agent.signature_hash_algorithm))
            sig_ok = True
        except Exception:  # noqa: BLE001
            sig_ok = False
        _check_mtls("chain: agent signature verifies under CA", sig_ok)

        _check_mtls("ca: BasicConstraints CA=true",
               ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca is True)
        _check_mtls("agent: BasicConstraints CA=false",
               agent.extensions.get_extension_for_class(x509.BasicConstraints).value.ca is False)

        eku = agent.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
        _check_mtls("agent: EKU clientAuth", ExtendedKeyUsageOID.CLIENT_AUTH in eku)
        _check_mtls("agent: EKU serverAuth", ExtendedKeyUsageOID.SERVER_AUTH in eku)
        _check_mtls("agent: CN carried", any(
            a.value == "test-agent" for a in agent.subject))

        if os.name == "posix":
            mode = os.stat(cfg["key"]).st_mode & 0o777
            _check_mtls("agent: key file mode 0600", mode == 0o600, oct(mode))

        ca_serial_before = ca.serial_number
        _, minted2 = _provision(tool, d)
        ca2 = x509.load_pem_x509_certificate(open(cfg["ca_cert"], "rb").read())
        _check_mtls("ca: reused on re-run (not minted)", minted2 is False)
        _check_mtls("ca: serial stable across re-runs",
               ca2.serial_number == ca_serial_before)

    passed = sum(1 for _, ok in _RESULTS_mtls if ok)
    total = len(_RESULTS_mtls)
    print(f"\n{passed}/{total} checks passed")
    return 0 if passed == total else 1


def _run_extra_mtls():
    try:
        return _main_mtls()
    except SystemExit as _e:
        return _e.code if _e.code is not None else 0



def _run_all_folded_crl_suites():
    rc = _run_extra_mtls()
    if rc not in (None, 0):
        import sys
        sys.exit(f"Folded test suite failed: exit code {rc}")

if __name__ == "__main__":
    _run_all_folded_crl_suites()
    sys.exit(main())
