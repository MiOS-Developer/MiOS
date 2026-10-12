#!/usr/bin/env python3
# AI-hint: Sibling unit tests for tools/check-runtime.py -- one suite per subcommand; the unittest suites run under one discovery pass, the script-style suites return their own verdict.
# AI-doc: usr/share/doc/mios/manual/tools.md
"""Sibling tests for the consolidated runtime, unit and resolver gates."""
from __future__ import annotations

import sys
import unittest



import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile

tcn__HERE = os.path.dirname(os.path.abspath(__file__))
tcn__spec = importlib.util.spec_from_file_location(
    "check_container_names", os.path.join(tcn__HERE, "check-runtime.py"))
tcn_M = importlib.util.module_from_spec(tcn__spec)
tcn__spec.loader.exec_module(tcn_M)

tcn__fails = 0

def tcn_check(name, cond, detail=""):
    global tcn__fails
    if cond:
        print(f"ok   - {name}")
    else:
        tcn__fails += 1
        print(f"FAIL - {name}" + (f" -- {detail}" if detail else ""))

def tcn_mkrepo(ssot, rendered, enable=None):
    """ssot: {unit: ContainerName|None}. rendered: {unit: ContainerName|None}."""
    root = tempfile.mkdtemp(prefix="cname-")
    os.makedirs(os.path.join(root, "usr/share/mios"), exist_ok=True)
    os.makedirs(os.path.join(root, "usr/share/containers/systemd"), exist_ok=True)
    lines = []
    for unit, cname in ssot.items():
        lines.append(f'[containers."{unit}".Container]')
        if cname is not None:
            lines.append(f'ContainerName = "{cname}"')
        lines.append("")
    if enable:
        lines.append("[quadlets.enable]")
        for unit, on in enable.items():
            lines.append(f'"{unit}" = {"true" if on else "false"}')
    open(os.path.join(root, tcn_M.cn_TOML), "w", encoding="utf-8").write("\n".join(lines) + "\n")
    for unit, cname in rendered.items():
        body = "[Container]\n" + (f"ContainerName={cname}\n" if cname is not None else "")
        open(os.path.join(root, "usr/share/containers/systemd", f"{unit}.container"),
             "w", encoding="utf-8").write(body)
    return root

def tcn_run(root):
    p = subprocess.run([sys.executable, os.path.join(tcn__HERE, "check-runtime.py"), "container-names"],
                       env={**os.environ, "MIOS_DRIFT_ROOT": root},
                       capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr

def tcn_main():
    roots = []

    r = tcn_mkrepo({"mios-a": "mios-a"}, {"mios-a": "mios-a"}); roots.append(r)
    rc, out = tcn_run(r)
    tcn_check("a matching pair passes", rc == 0, out)

    r = tcn_mkrepo({"mios-a": None}, {"mios-a": "mios-a"}); roots.append(r)
    rc, out = tcn_run(r)
    tcn_check("a MISSING ContainerName in the SSOT fails",
          rc == 1 and "systemd-mios-a" in out, out)

    r = tcn_mkrepo({"mios-a": "mios-a"}, {"mios-a": None}); roots.append(r)
    rc, out = tcn_run(r)
    tcn_check("a rendered unit with no ContainerName fails", rc == 1, out)

    r = tcn_mkrepo({"mios-a": "something-else"}, {"mios-a": "mios-a"}); roots.append(r)
    rc, out = tcn_run(r)
    tcn_check("an SSOT name that is not the unit name fails", rc == 1, out)

    r = tcn_mkrepo({"mios-a": "mios-a"}, {"mios-a": "something-else"}); roots.append(r)
    rc, out = tcn_run(r)
    tcn_check("a RENDERED name that is not the unit name fails independently", rc == 1, out)

    r = tcn_mkrepo({"mios-w@": "mios-w-%i"}, {"mios-w@": "mios-w-%i"}); roots.append(r)
    rc, out = tcn_run(r)
    tcn_check("a template unit naming <base>-%i passes", rc == 0, out)

    r = tcn_mkrepo({"mios-w@": "mios-w@"}, {"mios-w@": "mios-w@"}); roots.append(r)
    rc, out = tcn_run(r)
    tcn_check("a template unit naming its own key fails", rc == 1, out)

    r = tcn_mkrepo({"mios-a": "mios-a", "mios-off": "mios-off"}, {"mios-a": "mios-a"},
               enable={"mios-off": False}); roots.append(r)
    rc, out = tcn_run(r)
    tcn_check("a gated-off container may render nothing", rc == 0, out)
    tcn_check("the pass line counts the gated-off container", "gated-off=1" in out, out)

    r = tcn_mkrepo({"mios-a": "mios-a", "mios-off": None}, {"mios-a": "mios-a"},
               enable={"mios-off": False}); roots.append(r)
    rc, out = tcn_run(r)
    tcn_check("a gated-off container must STILL name itself correctly", rc == 1, out)

    r = tcn_mkrepo({"mios-a": "mios-a", "mios-b": "mios-b"}, {"mios-a": "mios-a"}); roots.append(r)
    rc, out = tcn_run(r)
    tcn_check("an ENABLED container with no rendered unit fails",
          rc == 1 and "regenerate" in out, out)

    r = tcn_mkrepo({}, {}); roots.append(r)
    rc, out = tcn_run(r)
    tcn_check("an empty tree fails rather than passing over nothing", rc == 1, out)

    tcn_check("expected_name: a plain unit names itself",
          tcn_M.cn_expected_name("mios-a") == "mios-a")
    tcn_check("expected_name: a template names the instantiated form",
          tcn_M.cn_expected_name("mios-w@") == "mios-w-%i")

    for r in roots:
        shutil.rmtree(r, ignore_errors=True)
    print(f"\n{'FAIL' if tcn__fails else 'PASS'}: {tcn__fails} failure(s)")
    return 1 if tcn__fails else 0


import os
import shutil
import subprocess
import sys
import tempfile

tpq__HERE = os.path.dirname(os.path.abspath(__file__))
tpq__fails = 0

def tpq_check(name, cond, detail=""):
    global tpq__fails
    if cond:
        print(f"ok   - {name}")
    else:
        tpq__fails += 1
        print(f"FAIL - {name}" + (f" -- {detail}" if detail else ""))

def tpq_run_tool(root):
    p = subprocess.run(
        [sys.executable, os.path.join(tpq__HERE, "check-runtime.py"), "privileged-quadlets"],
        env={**os.environ, "MIOS_DRIFT_ROOT": root},
        capture_output=True,
        text=True,
    )
    return p.returncode, p.stdout + p.stderr

def tpq_main():
    root = tempfile.mkdtemp(prefix="privileged-quadlets-test-")
    try:
        target_dir = os.path.join(root, "usr/share/mios")
        os.makedirs(target_dir, exist_ok=True)
        toml_path = os.path.join(target_dir, "mios.toml")

        # Copy real mios.toml to temp repo
        real_toml = os.path.join(tpq__HERE, "../usr/share/mios/mios.toml")
        shutil.copy(real_toml, toml_path)

        rc, out = tpq_run_tool(root)
        tpq_check("valid privileged quadlets register passes", rc == 0, f"rc={rc} out={out}")

        # Test un-commented entry failure
        with open(toml_path, "r", encoding="utf-8") as f:
            content = f.read()

        bad_content = content.replace(
            '"mios-ceph.container",                    # Ceph OSD/MON -- uid 0 for block devices',
            '"mios-ceph.container",',
        )
        with open(toml_path, "w", encoding="utf-8") as f:
            f.write(bad_content)

        rc, out = tpq_run_tool(root)
        tpq_check("unjustified root entry fails", rc != 0, f"rc={rc} out={out}")

    finally:
        shutil.rmtree(root, ignore_errors=True)

    if tpq__fails > 0:
        sys.exit(1)

"""Tests for the one-canonical-address-per-service gate."""

import os
import sys
import unittest
from importlib.machinery import SourceFileLoader

tsu__HERE = os.path.dirname(os.path.abspath(__file__))
tsu__ROOT = os.path.dirname(tsu__HERE)
tsu_mod = SourceFileLoader(
    "check_service_urls", os.path.join(tsu__HERE, "check-runtime.py")).load_module()

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

def tsu_data(ports=None, urls=None, register=None):
    d = {"ports": dict(ports or {}), "urls": dict(urls or {})}
    if register is not None:
        d["urls"]["non_addressable"] = list(register)
    return d

class tsu_TestPortKeys(unittest.TestCase):
    def test_stack_id_is_not_a_port(self):
        self.assertEqual(tsu_mod.su_port_keys(tsu_data({"a": 1, "stack_id": 0})), {"a"})

    def test_non_numeric_is_not_a_port(self):
        self.assertEqual(tsu_mod.su_port_keys(tsu_data({"a": 1, "categories": {}})), {"a"})

class tsu_TestCovered(unittest.TestCase):
    def test_templated_port_is_covered(self):
        d = tsu_data({"forge_http": 8400}, {"forge": "http://x:${MIOS_PORTS_FORGE_HTTP}"})
        self.assertEqual(tsu_mod.su_covered_ports(d), {"forge_http"})

    def test_one_url_may_cover_several_ports(self):
        d = tsu_data({"a": 1, "b": 2}, {"u": "${MIOS_PORTS_A}/${MIOS_PORTS_B}"})
        self.assertEqual(tsu_mod.su_covered_ports(d), {"a", "b"})

    def test_the_retired_port_spelling_is_not_the_canonical_address(self):
        # MIOS_PORT_<KEY> is an input alias of MIOS_PORTS_<KEY>; templating a
        # [urls] entry with it leaves the port without its canonical address.
        d = tsu_data({"forge_http": 8400}, {"forge": "http://x:${MIOS_PORT_FORGE_HTTP}"})
        self.assertEqual(tsu_mod.su_covered_ports(d), set())
        self.assertIn("'forge_http'", tsu_mod.su_classify(d)[0])

    def test_literal_port_number_does_not_count_as_covered(self):
        # A literal is exactly the hardcoding the gate wants replaced.
        d = tsu_data({"forge_http": 8400}, {"forge": "http://x:8400"})
        self.assertEqual(tsu_mod.su_covered_ports(d), set())

    def test_register_list_is_not_scanned_as_a_url(self):
        d = tsu_data({"a": 1}, {}, ["a"])
        self.assertEqual(tsu_mod.su_covered_ports(d), set())

class tsu_TestClassify(unittest.TestCase):
    def test_clean_tree_has_no_violations(self):
        d = tsu_data({"a": 1, "b": 2}, {"u": "http://x:${MIOS_PORTS_A}"}, ["b"])
        self.assertEqual(tsu_mod.su_classify(d), [])

    def test_unclassified_port_fails(self):
        d = tsu_data({"a": 1, "b": 2}, {"u": "http://x:${MIOS_PORTS_A}"}, [])
        self.assertEqual(len(tsu_mod.su_classify(d)), 1)
        self.assertIn("'b'", tsu_mod.su_classify(d)[0])

    def test_port_in_both_fails(self):
        d = tsu_data({"a": 1}, {"u": "http://x:${MIOS_PORTS_A}"}, ["a"])
        self.assertIn("two answers", tsu_mod.su_classify(d)[0])

    def test_register_naming_a_missing_port_fails(self):
        d = tsu_data({"a": 1}, {"u": "http://x:${MIOS_PORTS_A}"}, ["ghost"])
        self.assertIn("not a [ports] key", tsu_mod.su_classify(d)[0])

    def test_duplicate_register_entry_fails(self):
        d = tsu_data({"a": 1, "b": 2}, {"u": "http://x:${MIOS_PORTS_A}"}, ["b", "b"])
        self.assertIn("twice", tsu_mod.su_classify(d)[0])

    def test_empty_port_table_fails_rather_than_passing_vacuously(self):
        self.assertIn("vacuously", tsu_mod.su_classify(tsu_data({}, {}, []))[0])

    def test_register_whitespace_and_blanks_are_ignored(self):
        d = tsu_data({"a": 1, "b": 2}, {"u": "${MIOS_PORTS_A}"}, [" b ", "", "  "])
        self.assertEqual(tsu_mod.su_classify(d), [])

class tsu_TestShippedTree(unittest.TestCase):
    def test_the_real_ssot_classifies_every_port(self):
        with open(os.path.join(tsu__ROOT, tsu_mod.su_TOML), "rb") as fh:
            real = tomllib.load(fh)
        self.assertEqual(tsu_mod.su_classify(real), [])

    def test_every_register_entry_is_a_real_port(self):
        with open(os.path.join(tsu__ROOT, tsu_mod.su_TOML), "rb") as fh:
            real = tomllib.load(fh)
        self.assertTrue(set(tsu_mod.su_register(real)) <= tsu_mod.su_port_keys(real))

    def test_the_register_is_not_empty_yet(self):
        # Guards the test itself: if the register ever empties, these assertions
        # stop proving anything and this line is the reminder to delete them.
        with open(os.path.join(tsu__ROOT, tsu_mod.su_TOML), "rb") as fh:
            real = tomllib.load(fh)
        self.assertGreater(len(tsu_mod.su_register(real)), 0)

class tsu_TestBrowserOpenable(unittest.TestCase):
    """[urls] is what a person clicks -- one meaning, not two."""

    def test_an_http_entry_is_clean(self):
        self.assertEqual(tsu_mod.su_browser_openable(
            {"urls": {"forge": "http://localhost:${MIOS_PORTS_FORGE_HTTP}"}}), [])

    def test_an_https_entry_is_clean(self):
        self.assertEqual(tsu_mod.su_browser_openable(
            {"urls": {"cockpit": "https://localhost:${MIOS_PORTS_COCKPIT}"}}), [])

    def test_a_dsn_fails(self):
        # [urls].pgvector shipped as a postgresql:// DSN, which made the table
        # mean both "a tile" and "an inter-service address".
        out = tsu_mod.su_browser_openable(
            {"urls": {"pgvector": "postgresql://mios@localhost:8600/mios"}})
        self.assertTrue(out)
        self.assertIn("postgresql", out[0])

    def test_a_non_url_fails(self):
        self.assertTrue(tsu_mod.su_browser_openable({"urls": {"x": "localhost:8600"}}))

    def test_the_register_list_is_not_treated_as_a_url(self):
        self.assertEqual(tsu_mod.su_browser_openable(
            {"urls": {"non_addressable": ["a", "b"]}}), [])

    def test_the_shipped_table_is_browser_openable(self):
        import os
        with open(os.path.join(tsu__ROOT, "usr/share/mios/mios.toml"), "rb") as fh:
            self.assertEqual(tsu_mod.su_browser_openable(tomllib.load(fh)), [])

class tsu_TestBarePortAddresses(unittest.TestCase):
    """An address an /etc/mios overlay cannot move is a service that can never
    be offloaded -- which is the whole of MiOS-Metal."""

    def test_a_bare_port_localhost_url_fails(self):
        out = tsu_mod.su_bare_port_addresses(
            {"ports": {"llm_light": 8500},
             "ai": {"endpoint": "http://localhost:8500/v1"}})
        self.assertTrue(out)
        self.assertIn("MIOS_PORTS_LLM_LIGHT", out[0])

    def test_the_loopback_spelling_is_caught_too(self):
        self.assertTrue(tsu_mod.su_bare_port_addresses(
            {"ports": {"crawl4ai": 8810},
             "x": {"y": "http://127.0.0.1:8810/crawl"}}))

    def test_a_templated_url_is_clean(self):
        self.assertEqual(tsu_mod.su_bare_port_addresses(
            {"ports": {"llm_light": 8500},
             "ai": {"endpoint": "http://localhost:${MIOS_PORTS_LLM_LIGHT}/v1"}}), [])

    def test_a_port_that_is_not_ours_is_ignored(self):
        self.assertEqual(tsu_mod.su_bare_port_addresses(
            {"ports": {"llm_light": 8500},
             "x": {"y": "http://localhost:9999/"}}), [])

    def test_rendered_unit_bodies_are_out_of_scope(self):
        # units/containers carry ${VAR:-N} defaults by design; check_port_fallbacks
        # owns those, and double-owning would make both registers lie.
        self.assertEqual(tsu_mod.su_bare_port_addresses(
            {"ports": {"llm_light": 8500},
             "units": {"x.service": {"Service": {"Exec": "--listen :8500"}}}}), [])

    def test_a_non_local_host_is_not_this_rule(self):
        self.assertEqual(tsu_mod.su_bare_port_addresses(
            {"ports": {"llm_light": 8500},
             "x": {"y": "http://blade-01:8500/v1"}}), [])

    def test_the_shipped_tree_has_no_unmovable_address(self):
        import os
        with open(os.path.join(tsu__ROOT, "usr/share/mios/mios.toml"), "rb") as fh:
            self.assertEqual(tsu_mod.su_bare_port_addresses(tomllib.load(fh)), [])

"""Assert the governor-coverage gate fails for each defect class it guards."""
import os
import subprocess
import sys
import tempfile

tdg_HERE = os.path.dirname(os.path.abspath(__file__))
tdg_GATE = os.path.join(tdg_HERE, "check-runtime.py")
tdg_FAILED = 0

tdg_SSOT = """[daemon]
knob_a = 1
[budget]
autonomous_max_inflight = 1
"""
tdg_GOOD_DAEMON = '''#!/usr/bin/env python3
A = _cfg_num("ENV_A", "knob_a", 1.0)
def worker_loop():
    while True:
        if _pressure_should_skip("worker"):
            continue
'''
tdg_CHAT_OK = '_budget_num("MIOS_BUDGET_AUTO_MAX_INFLIGHT", "autonomous_max_inflight", 1)\n'

def tdg_build(tmp, daemon=tdg_GOOD_DAEMON, ssot=tdg_SSOT, chat=tdg_CHAT_OK, extra=None):
    os.makedirs(os.path.join(tmp, "usr/libexec/mios"), exist_ok=True)
    os.makedirs(os.path.join(tmp, "usr/share/mios"), exist_ok=True)
    os.makedirs(os.path.join(tmp, "usr/lib/mios/agent-pipe/mios_pipe/routing"), exist_ok=True)
    open(os.path.join(tmp, "usr/libexec/mios/mios-daemon"), "w").write(daemon)
    open(os.path.join(tmp, "usr/share/mios/mios.toml"), "w").write(ssot)
    open(os.path.join(tmp, "usr/lib/mios/agent-pipe/mios_pipe/routing/chat.py"), "w").write(chat)
    for name, body in (extra or {}).items():
        open(os.path.join(tmp, "usr/libexec/mios", name), "w").write(body)
    return tmp

def tdg_case(label, want_zero, **kw):
    global tdg_FAILED
    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ, MIOS_ROOT=tdg_build(tmp, **kw))
        rc = subprocess.run([sys.executable, tdg_GATE, "daemon-governor"], env=env,
                            capture_output=True, text=True).returncode
    ok = (rc == 0) if want_zero else (rc != 0)
    print(f"[{'PASS' if ok else 'FAIL'}] {label} (exit {rc})")
    if not ok:
        tdg_FAILED += 1

def tdg_main():

    tdg_case("complete governor passes", True)
    tdg_case("ungated autonomous loop fails", False,
         daemon=tdg_GOOD_DAEMON + '\ndef rogue_loop():\n    while True:\n        pass\n')
    tdg_case("declared-but-dead knob fails", False,
         daemon='def worker_loop():\n    if _pressure_should_skip("w"): pass\n')
    tdg_case("knob only in a COMMENT is not a consumer", False,
         daemon='# mentions "knob_a" in prose only\ndef worker_loop():\n    if _pressure_should_skip("w"): pass\n')
    tdg_case("knob only in a TEST file is not a consumer", False,
         daemon='def worker_loop():\n    if _pressure_should_skip("w"): pass\n',
         extra={"test_mios_thing.py": 'X = "knob_a"\n'})
    tdg_case("drifted budget fallback fails", False,
         chat='_budget_num("MIOS_BUDGET_AUTO_MAX_INFLIGHT", "autonomous_max_inflight", 9)\n')
    tdg_case("exempt server loop needs no gate", True,
         daemon=tdg_GOOD_DAEMON + '\ndef daemon_agent_server_loop():\n    while True:\n        pass\n')

    print(f"\n{7 - tdg_FAILED}/7 checks pass")

    return 1 if tdg_FAILED else 0


import importlib.util
import os
import shutil
import sys
import tempfile

tfdo__HERE = os.path.dirname(os.path.abspath(__file__))
tfdo__spec = importlib.util.spec_from_file_location(
    "check_firstboot_degrade_open",
    os.path.join(tfdo__HERE, "check-runtime.py"))
tfdo_M = importlib.util.module_from_spec(tfdo__spec)
tfdo__spec.loader.exec_module(tfdo_M)

tfdo__fails = 0


def tfdo_check(name, cond, detail=""):
    global tfdo__fails
    if cond:
        print("ok   - %s" % name)
    else:
        tfdo__fails += 1
        print("FAIL - %s%s" % (name, " -- %s" % detail if detail else ""))


def tfdo_scan_text(body):
    """Run the real scanner over a throwaway firstboot script."""
    root = tempfile.mkdtemp(prefix="mios-degrade-")
    try:
        d = os.path.join(root, "usr", "libexec", "mios")
        os.makedirs(d)
        path = os.path.join(d, "demo-firstboot.sh")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
        return tfdo_M.fdo_scan(path)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def tfdo_run_main(body=None):
    """Run main() against a temp root; None means an empty scan set."""
    root = tempfile.mkdtemp(prefix="mios-degrade-main-")
    prev = os.environ.get("MIOS_DRIFT_ROOT")
    try:
        d = os.path.join(root, "usr", "libexec", "mios")
        os.makedirs(d)
        if body is not None:
            with open(os.path.join(d, "demo-firstboot.sh"), "w",
                      encoding="utf-8") as fh:
                fh.write(body)
        os.environ["MIOS_DRIFT_ROOT"] = root
        return tfdo_M.fdo_main()
    finally:
        if prev is None:
            os.environ.pop("MIOS_DRIFT_ROOT", None)
        else:
            os.environ["MIOS_DRIFT_ROOT"] = prev
        shutil.rmtree(root, ignore_errors=True)


def tfdo_t_unguarded_egress_is_caught():
    bad = tfdo_scan_text("set -euo pipefail\ncurl -sfL http://x/y -o /tmp/y\n")
    tfdo_check("unguarded curl under set -e is a finding", len(bad) == 1, repr(bad))


def tfdo_t_guarded_egress_passes():
    bad = tfdo_scan_text("set -euo pipefail\ncurl -sfL http://x/y -o /tmp/y || true\n")
    tfdo_check("|| true guards the call", bad == [], repr(bad))


def tfdo_t_unrelated_guard_does_not_certify_file():
    # The defect this gate replaced: any '|| true' anywhere passed the file.
    bad = tfdo_scan_text("set -euo pipefail\nrm -f /tmp/s || true\n"
                    "curl -sfL http://x/y -o /tmp/y\n")
    tfdo_check("an unrelated '|| true' elsewhere does not certify the script",
          len(bad) == 1, repr(bad))


def tfdo_t_no_errexit_is_not_a_finding():
    bad = tfdo_scan_text("curl -sfL http://x/y -o /tmp/y\n")
    tfdo_check("without set -e an unguarded fetch cannot abort boot", bad == [],
          repr(bad))


def tfdo_t_indented_set_plus_e_does_not_leak():
    # An indented 'set +e' is inside a function or subshell and must not exempt
    # later top-level lines.
    bad = tfdo_scan_text("set -euo pipefail\nf() {\n    set +e\n}\n"
                    "curl -sfL http://x/y -o /tmp/y\n")
    tfdo_check("indented 'set +e' does not disable errexit for later lines",
          len(bad) == 1, repr(bad))


def tfdo_t_toplevel_set_plus_e_does_exempt():
    bad = tfdo_scan_text("set -euo pipefail\nset +e\ncurl -sfL http://x/y -o /tmp/y\n")
    tfdo_check("column-0 'set +e' does exempt what follows", bad == [], repr(bad))


def tfdo_t_continuation_guard_is_credited():
    bad = tfdo_scan_text("set -euo pipefail\n(curl -sf \\n    http://x/y) || true\n")
    tfdo_check("a guard after a continuation is seen", bad == [], repr(bad))


def tfdo_t_narration_is_not_a_call():
    bad = tfdo_scan_text('set -euo pipefail\necho "run: curl -sfL http://x/y"\n')
    tfdo_check("a fetch named inside an echo string is not a call", bad == [],
          repr(bad))


def tfdo_t_case_pattern_does_not_desync_join():
    # An unmatched ")" in a case pattern must not drive paren depth negative.
    bad = tfdo_scan_text("set -euo pipefail\ncase $x in\n  *.pyc) ;;\nesac\n"
                    "curl -sfL http://x/y -o /tmp/y\n")
    tfdo_check("a case pattern does not desynchronise the line join", len(bad) == 1,
          repr(bad))


def tfdo_t_errexit_variants_register():
    for form in ("set -e", "set -euo pipefail", "set -o errexit"):
        bad = tfdo_scan_text("%s\ncurl -sfL http://x/y -o /tmp/y\n" % form)
        tfdo_check("errexit form %r is recognised" % form, len(bad) == 1, repr(bad))


def tfdo_t_empty_scan_set_fails():
    tfdo_check("an empty scan set is a failure, not a pass", tfdo_run_main(None) == 1)


def tfdo_t_main_returns_zero_when_clean():
    tfdo_check("main() returns 0 on a clean tree",
          tfdo_run_main("set -euo pipefail\ncurl -sf http://x/y || true\n") == 0)


def tfdo_main():
    tfdo_t_unguarded_egress_is_caught()
    tfdo_t_guarded_egress_passes()
    tfdo_t_unrelated_guard_does_not_certify_file()
    tfdo_t_no_errexit_is_not_a_finding()
    tfdo_t_indented_set_plus_e_does_not_leak()
    tfdo_t_toplevel_set_plus_e_does_exempt()
    tfdo_t_continuation_guard_is_credited()
    tfdo_t_narration_is_not_a_call()
    tfdo_t_case_pattern_does_not_desync_join()
    tfdo_t_errexit_variants_register()
    tfdo_t_empty_scan_set_fails()
    tfdo_t_main_returns_zero_when_clean()
    print("\n%d FAILED" % tfdo__fails if tfdo__fails else "\nok")
    return 1 if tfdo__fails else 0


import importlib.util
import os
import shutil
import sys
import tempfile

tfp__HERE = os.path.dirname(os.path.abspath(__file__))
tfp__spec = importlib.util.spec_from_file_location(
    "check_firstboot_provisioners",
    os.path.join(tfp__HERE, "check-runtime.py"))
tfp_M = importlib.util.module_from_spec(tfp__spec)
tfp__spec.loader.exec_module(tfp_M)

tfp__fails = 0
tfp_SENTINEL = "/var/lib/mios/.demo-done"
tfp_VARDIR = "/var/lib/mios/demo"

def tfp_check(name, cond, detail=""):
    global tfp__fails
    if cond:
        print(f"ok   - {name}")
    else:
        tfp__fails += 1
        print(f"FAIL - {name}" + (f" -- {detail}" if detail else ""))

def tfp_mkroot(*, fetcher=True, sentinel_in_fetcher=True, execstart=None,
           condition=True, condition_path=None, preset=True, tmpfiles=True):
    root = tempfile.mkdtemp(prefix="fbprov-")
    os.makedirs(os.path.join(root, "usr/libexec/mios"), exist_ok=True)
    os.makedirs(os.path.join(root, tfp_M.fp_UNIT_DIR), exist_ok=True)
    os.makedirs(os.path.join(root, "usr/lib/systemd/system-preset"), exist_ok=True)
    os.makedirs(os.path.join(root, tfp_M.fp_TMPFILES_DIR), exist_ok=True)

    if fetcher:
        body = "#!/usr/bin/env python3\n"
        if sentinel_in_fetcher:
            body += f'SENTINEL = "{tfp_SENTINEL}"\n'
        open(os.path.join(root, "usr/libexec/mios/demo-firstboot"), "w").write(body)

    lines = ["[Unit]", "Description=Demo"]
    if condition:
        lines.append("ConditionPathExists=!" + (condition_path or tfp_SENTINEL))
    lines += ["", "[Service]", "Type=oneshot",
              "ExecStart=" + (execstart or "/usr/libexec/mios/demo-firstboot")]
    open(os.path.join(root, tfp_M.fp_UNIT_DIR, "demo-firstboot.service"), "w").write(
        "\n".join(lines) + "\n")

    open(os.path.join(root, tfp_M.fp_PRESET), "w").write(
        "enable demo-firstboot.service\n" if preset else "enable something-else.service\n")

    open(os.path.join(root, tfp_M.fp_TMPFILES_DIR, "demo.conf"), "w").write(
        f"d {tfp_VARDIR} 0750 827 827 -\n" if tmpfiles else "# nothing declared\n")
    return root

def tfp_run(root):
    declared = tfp_M.fp_tmpfiles_dirs(root)
    return tfp_M.fp_check_one(root, "demo-firstboot.service",
                       "usr/libexec/mios/demo-firstboot", (tfp_VARDIR,), declared)

def tfp_t_whole_triple_passes():
    r = tfp_mkroot()
    try:
        tfp_check("a whole triple passes", tfp_run(r) == [], str(tfp_run(r)))
    finally:
        shutil.rmtree(r, ignore_errors=True)

def tfp_t_missing_fetcher():
    r = tfp_mkroot(fetcher=False)
    try:
        bad = tfp_run(r)
        tfp_check("a missing fetcher fails", len(bad) == 1 and "does not exist" in bad[0], str(bad))
    finally:
        shutil.rmtree(r, ignore_errors=True)

def tfp_t_wrong_execstart():
    r = tfp_mkroot(execstart="/usr/bin/true")
    try:
        bad = tfp_run(r)
        tfp_check("an ExecStart pointing elsewhere fails",
              any("ExecStart does not run" in b for b in bad), str(bad))
    finally:
        shutil.rmtree(r, ignore_errors=True)

def tfp_t_no_condition_gate():
    r = tfp_mkroot(condition=False)
    try:
        bad = tfp_run(r)
        tfp_check("no ConditionPathExists gate fails (would re-run every boot)",
              any("no ConditionPathExists" in b for b in bad), str(bad))
    finally:
        shutil.rmtree(r, ignore_errors=True)

def tfp_t_sentinel_never_written():
    r = tfp_mkroot(sentinel_in_fetcher=False)
    try:
        bad = tfp_run(r)
        tfp_check("a gate on a sentinel the fetcher never writes fails",
              any("never names that path" in b for b in bad), str(bad))
    finally:
        shutil.rmtree(r, ignore_errors=True)

def tfp_t_gate_on_a_different_path():
    r = tfp_mkroot(condition_path="/var/lib/mios/.some-other-sentinel")
    try:
        bad = tfp_run(r)
        tfp_check("a gate on the WRONG sentinel path fails",
              any("never names that path" in b for b in bad), str(bad))
    finally:
        shutil.rmtree(r, ignore_errors=True)

def tfp_t_not_in_preset():
    r = tfp_mkroot(preset=False)
    try:
        bad = tfp_run(r)
        tfp_check("a unit absent from the preset fails",
              any("not enabled in" in b for b in bad), str(bad))
    finally:
        shutil.rmtree(r, ignore_errors=True)

def tfp_t_undeclared_var_dir():
    r = tfp_mkroot(tmpfiles=False)
    try:
        bad = tfp_run(r)
        tfp_check("an undeclared /var dir fails (Law 2)",
              any("Architectural Law 2" in b for b in bad), str(bad))
    finally:
        shutil.rmtree(r, ignore_errors=True)

def tfp_main():
    tfp_t_whole_triple_passes()
    tfp_t_missing_fetcher()
    tfp_t_wrong_execstart()
    tfp_t_no_condition_gate()
    tfp_t_sentinel_never_written()
    tfp_t_gate_on_a_different_path()
    tfp_t_not_in_preset()
    tfp_t_undeclared_var_dir()
    print(f"\n{tfp__fails} FAILED" if tfp__fails else "\nok")
    return 1 if tfp__fails else 0

import importlib.util
import os
import unittest

tvi__HERE = os.path.dirname(os.path.abspath(__file__))
tvi__ROOT = os.path.dirname(tvi__HERE)

def tvi__load():
    spec = importlib.util.spec_from_file_location(
        "check_verify_images", os.path.join(tvi__HERE, "check-runtime.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

tvi_MOD = tvi__load()

class tvi_TestCheckVerifyImages(unittest.TestCase):
    def setUp(self):
        os.environ["MIOS_DRIFT_ROOT"] = tvi__ROOT

    def test_the_shipped_tree_passes(self):
        self.assertEqual(0, tvi_MOD.vi_main() if tvi_MOD.vi_main.__code__.co_argcount == 0 else tvi_MOD.vi_main([]))

    def test_the_recipe_still_delegates_to_the_verifier(self):
        """A recipe that stops calling the script is the regression to catch."""
        just = open(os.path.join(tvi__ROOT, "Justfile"), encoding="utf-8", errors="replace").read()
        self.assertIn("tools/verify-images.py", just)

    def test_publish_still_depends_on_verify_images(self):
        just = open(os.path.join(tvi__ROOT, "Justfile"), encoding="utf-8", errors="replace").read()
        line = [l for l in just.split(chr(10)) if l.startswith("publish:")]
        self.assertTrue(line, "no publish recipe")
        self.assertIn("verify-images", line[0])


def main() -> int:
    rc = 0 if unittest.main(argv=[sys.argv[0]], exit=False).result.wasSuccessful() else 1
    for fn in (tcn_main, tpq_main, tdg_main, tfdo_main, tfp_main, ):
        rc |= (fn() or 0)
    return rc


if __name__ == "__main__":
    sys.exit(main())
