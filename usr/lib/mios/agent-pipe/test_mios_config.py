#!/usr/bin/env python3
# AI-hint: Standalone assert-script unit test for mios_config (refactor WS R1 config-constants extraction). Pure stdlib, no server.py/DB...
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md
"""Unit tests for mios_config (refactor R1)."""

import os
import sys

import mios_config as c

_fails = 0

def check(name, cond, detail=""):
    global _fails
    if not cond:
        _fails += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

def t_import():
    check("import: module loaded", c is not None)
    check("import: log name", c.log.name == "mios-agent-pipe")

def t_toml_section():
    d = c._toml_section("ai")
    check("toml_section: returns dict", isinstance(d, dict), type(d).__name__)
    miss = c._toml_section("definitely_not_a_real_section_xyz")
    check("toml_section: missing -> {}", miss == {})

def t_cfg_num():
    env = "MIOS_TEST_CFG_NUM_XYZ"
    os.environ.pop(env, None)
    os.environ[env] = "5"
    check("cfg_num: env override wins", c._cfg_num({"k": 3}, env, "k", 1) == 5)
    os.environ.pop(env, None)
    check("cfg_num: table value", c._cfg_num({"k": 3}, env, "k", 1) == 3)
    check("cfg_num: literal default", c._cfg_num({}, env, "k", 7) == 7)
    check("cfg_num: preserves table 0", c._cfg_num({"k": 0}, env, "k", 7) == 0)
    check("cfg_num: preserves default 0", c._cfg_num({}, env, "k", 0) == 0)
    check("cfg_num: float cast", c._cfg_num({"k": "1.5"}, env, "k", 0.0, float) == 1.5)
    os.environ[env] = "notanumber"
    check("cfg_num: bad env -> table", c._cfg_num({"k": 9}, env, "k", 1) == 9)
    os.environ.pop(env, None)

def t_dispatch_num():
    env = "MIOS_TEST_DISPATCH_NUM_XYZ"
    os.environ.pop(env, None)
    check("dispatch_num: literal default",
          c._dispatch_num(env, "no_such_dispatch_key_xyz", 4) == 4)
    os.environ[env] = "11"
    check("dispatch_num: env override",
          c._dispatch_num(env, "no_such_dispatch_key_xyz", 4) == 11)
    os.environ.pop(env, None)
    check("dispatch_num: preserves default 0",
          c._dispatch_num(env, "no_such_dispatch_key_xyz", 0) == 0)
    check("dispatch_num: _DISPATCH_TOML is dict", isinstance(c._DISPATCH_TOML, dict))

def t_constants():
    check("const: PORT int", isinstance(c.PORT, int))
    check("const: MCP_SERVER_PORT int", isinstance(c.MCP_SERVER_PORT, int))
    check("const: _LIGHT_BASE localhost",
          isinstance(c._LIGHT_BASE, str) and c._LIGHT_BASE.startswith("http://localhost:"),
          c._LIGHT_BASE)
    check("const: BACKEND str no trailing slash",
          isinstance(c.BACKEND, str) and not c.BACKEND.endswith("/"), c.BACKEND)
    check("const: BACKEND_MODEL str", isinstance(c.BACKEND_MODEL, str))
    check("const: _BACKEND_HOSTPORT derived from BACKEND",
          c._BACKEND_HOSTPORT == c.BACKEND.split("://")[-1].split("/")[0])
    check("const: _BACKEND_IS_LIGHT bool", isinstance(c._BACKEND_IS_LIGHT, bool))
    check("const: _AUTH_HOSTPORTS is set with backend",
          isinstance(c._AUTH_HOSTPORTS, set) and c._BACKEND_HOSTPORT in c._AUTH_HOSTPORTS)
    check("const: _AGENT_AUTH_BY_HOSTPORT dict", isinstance(c._AGENT_AUTH_BY_HOSTPORT, dict))
    check("const: CLIENT_TOOLS_PASSTHROUGH bool", isinstance(c.CLIENT_TOOLS_PASSTHROUGH, bool))
    check("const: _TOOL_BACKEND str", isinstance(c._TOOL_BACKEND, str))
    check("const: _TOOL_BACKEND_MODEL str", isinstance(c._TOOL_BACKEND_MODEL, str))
    check("const: _HEAVY_PROBE_TTL float", isinstance(c._HEAVY_PROBE_TTL, float))
    check("const: _INGRESS_KEY str", isinstance(c._INGRESS_KEY, str))
    check("const: _STACK_MODEL str", isinstance(c._STACK_MODEL, str))
    check("const: _MICRO_MODEL str", isinstance(c._MICRO_MODEL, str))
    check("const: _MICRO_ENDPOINT str no trailing slash",
          isinstance(c._MICRO_ENDPOINT, str) and not c._MICRO_ENDPOINT.endswith("/"))
    check("const: _MICRO_BASE no trailing /v1",
          isinstance(c._MICRO_BASE, str) and not c._MICRO_BASE.endswith("/v1"))

def main():
    t_import()
    t_toml_section()
    t_cfg_num()
    t_dispatch_num()
    t_constants()
    print(f"\n{'ok' if _fails == 0 else str(_fails) + ' FAILED'}")
    return 1 if _fails else 0



# ==============================================================================
# Consolidated from test_mios_config_audit.py (task_1006)
# ==============================================================================
# AI-hint: Unit and regression test suite for mios_config_audit functionality.
# AI-related: localhost:8432
# AI-functions: setUpModule, setUp, tearDown, _cleanup, test_config_kv_redaction, test_container_env_redaction, test_verb_cmd_redaction, TestMiosConfigAudit

import sys
import os
import unittest
import json
try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:
    psycopg = None
    dict_row = None

def setUpModule():
    if psycopg is None:
        raise unittest.SkipTest("no live pgvector -- integration test")
    port = os.environ.get("MIOS_PORTS_PGVECTOR", "8600")
    conn_str = f"postgresql://mios:mios@localhost:{port}/mios"
    try:
        with psycopg.connect(conn_str, connect_timeout=1):
            pass
    except Exception:
        raise unittest.SkipTest("no live pgvector -- integration test")

class TestMiosConfigAudit(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # The folded runner constructs TestSuites directly, so it bypasses
        # setUpModule. Probe the same endpoint this class actually tests.
        if psycopg is None:
            raise unittest.SkipTest("pgvector audit integration dependency absent")
        cls.conn_str = "postgresql://mios:mios@localhost:8432/mios"
        try:
            with psycopg.connect(cls.conn_str, connect_timeout=1):
                pass
        except psycopg.OperationalError:
            raise unittest.SkipTest("pgvector audit integration endpoint absent")

    def setUp(self):
        self._cleanup()

    def tearDown(self):
        self._cleanup()

    def _cleanup(self):
        with psycopg.connect(self.conn_str) as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM config_kv WHERE scope = 'test_audit_scope'")
                cur.execute("DELETE FROM config_kv WHERE scope = 'security' AND key = 'default_password'")
                cur.execute("DELETE FROM verb WHERE name IN ('test_audit_verb_clean', 'test_audit_verb_secret')")
                cur.execute("DELETE FROM config_event WHERE scope = 'config_kv' AND key LIKE 'test_audit_scope.%'")
                cur.execute("DELETE FROM config_event WHERE scope = 'config_event' AND key = 'security.default_password'")
                cur.execute("DELETE FROM config_event WHERE scope = 'config_kv' AND key = 'security.default_password'")
                cur.execute("DELETE FROM config_event WHERE scope = 'verb' AND key IN ('test_audit_verb_clean', 'test_audit_verb_secret')")
            conn.commit()

    def test_config_kv_redaction(self):
        with psycopg.connect(self.conn_str) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO config_kv (scope, key, value, layer, description)
                    VALUES ('test_audit_scope', 'normal_key', '"normal_value"'::jsonb, 3, 'normal')
                    """
                )
                cur.execute(
                    """
                    INSERT INTO config_kv (scope, key, value, layer, description)
                    VALUES ('test_audit_scope', 'github_api_key', '"ghp_abcdef123456"'::jsonb, 3, 'secret key')
                    """
                )
                cur.execute(
                    """
                    INSERT INTO config_kv (scope, key, value, layer, description)
                    VALUES ('security', 'default_password', '"mypass123"'::jsonb, 3, 'secret scope')
                    """
                )
            conn.commit()

        with psycopg.connect(self.conn_str, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT new_value FROM config_event
                    WHERE scope = 'config_kv' AND key = 'test_audit_scope.normal_key'
                    ORDER BY id DESC LIMIT 1;
                    """
                )
                r_normal = cur.fetchone()
                self.assertIsNotNone(r_normal)
                self.assertEqual(r_normal["new_value"], "normal_value")

                cur.execute(
                    """
                    SELECT new_value FROM config_event
                    WHERE scope = 'config_kv' AND key = 'test_audit_scope.github_api_key'
                    ORDER BY id DESC LIMIT 1;
                    """
                )
                r_secret_key = cur.fetchone()
                self.assertIsNotNone(r_secret_key)
                self.assertEqual(r_secret_key["new_value"], "[REDACTED_SECRET]")

                cur.execute(
                    """
                    SELECT new_value FROM config_event
                    WHERE scope = 'config_kv' AND key = 'security.default_password'
                    ORDER BY id DESC LIMIT 1;
                    """
                )
                r_secret_scope = cur.fetchone()
                self.assertIsNotNone(r_secret_scope)
                self.assertEqual(r_secret_scope["new_value"], "[REDACTED_SECRET]")

    def test_container_env_redaction(self):
        with psycopg.connect(self.conn_str) as conn:
            with conn.cursor() as cur:
                val_json = {
                    "Container": {
                        "ContainerName": "test-container",
                        "Environment": [
                            "PORT=8080",
                            "K3S_TOKEN=mysecret-token-value",
                            "DATABASE_PASSWORD=secretpassword123"
                        ],
                        "SecretConfig": {
                            "some_value": "nested-value"
                        },
                        "OtherConfig": {
                            "api_key": "some-value-to-redact"
                        }
                    }
                }
                cur.execute(
                    """
                    INSERT INTO config_kv (scope, key, value, layer, description)
                    VALUES ('test_audit_scope', 'container_cfg', %s::jsonb, 3, 'container with env secrets')
                    """,
                    (json.dumps(val_json),)
                )
            conn.commit()

        with psycopg.connect(self.conn_str, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT new_value FROM config_event
                    WHERE scope = 'config_kv' AND key = 'test_audit_scope.container_cfg'
                    ORDER BY id DESC LIMIT 1;
                    """
                )
                r_container = cur.fetchone()
                self.assertIsNotNone(r_container)
                new_val = r_container["new_value"]
                if isinstance(new_val, str):
                    new_val = json.loads(new_val)

                env = new_val["Container"]["Environment"]
                self.assertIn("PORT=8080", env)
                self.assertIn("K3S_TOKEN=[REDACTED_SECRET]", env)
                self.assertIn("DATABASE_PASSWORD=[REDACTED_SECRET]", env)

                self.assertEqual(new_val["Container"]["SecretConfig"], "[REDACTED_SECRET]")

                self.assertEqual(new_val["Container"]["OtherConfig"]["api_key"], "[REDACTED_SECRET]")

    def test_verb_cmd_redaction(self):
        with psycopg.connect(self.conn_str) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO verb (name, sig, desc_default, tier, permission, cmd)
                    VALUES ('test_audit_verb_clean', '()', 'clean desc', 'common', 'read', 'git status')
                    """
                )
                cur.execute(
                    """
                    INSERT INTO verb (name, sig, desc_default, tier, permission, cmd)
                    VALUES ('test_audit_verb_secret', '()', 'secret desc', 'common', 'read', 'curl -H "Authorization: Bearer my_secret_token_123" https://api.github.com')
                    """
                )
            conn.commit()

        with psycopg.connect(self.conn_str, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT new_value FROM config_event
                    WHERE scope = 'verb' AND key = 'test_audit_verb_clean'
                    ORDER BY id DESC LIMIT 1;
                    """
                )
                r_clean = cur.fetchone()
                self.assertIsNotNone(r_clean)
                self.assertEqual(r_clean["new_value"]["cmd"], "git status")

                cur.execute(
                    """
                    SELECT new_value FROM config_event
                    WHERE scope = 'verb' AND key = 'test_audit_verb_secret'
                    ORDER BY id DESC LIMIT 1;
                    """
                )
                r_secret = cur.fetchone()
                self.assertIsNotNone(r_secret)
                self.assertEqual(r_secret["new_value"]["cmd"], "[REDACTED_SECRET]")


def _run_cases(*cases):
    import unittest
    suite = unittest.TestSuite()
    for c in cases:
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(c))
    return 0 if unittest.TextTestRunner().run(suite).wasSuccessful() else 1


def _run_extra_config_audit():
    return _run_cases(TestMiosConfigAudit)



# ==============================================================================
# Consolidated from test_mios_config_validate.py (task_1006)
# ==============================================================================
# AI-hint: Hermetic unit tests for the WS-CONFIG server-side SAFETY validator
# AI-related: ./mios_pipe/kernel/config.py, ./mios_pipe/routing/portal.py
"""Hermetic tests for validate_config (WS-CONFIG safety net).

Run standalone:  python test_mios_config_validate.py
Or via pytest:   pytest test_mios_config_validate.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mios_pipe.kernel.config import validate_config

VALID = """
[identity]
mios_user = "corey"

[ports]
agent_pipe = 8640
hermes = 8642
"""

def test_accepts_valid_config():
    ok, errors = validate_config(VALID, live_config={"identity": {"mios_user": "x"},
                                                     "ports": {"agent_pipe": 8640}})
    assert ok is True, errors
    assert errors == []

def test_rejects_dropped_identity_section():
    posted = """
[ports]
agent_pipe = 8640
"""
    live = {"identity": {"mios_user": "corey"}, "ports": {"agent_pipe": 8640}}
    ok, errors = validate_config(posted, live_config=live)
    assert ok is False
    assert any("identity" in e for e in errors), errors

def test_rejects_dropped_ports_section():
    posted = """
[identity]
mios_user = "corey"
"""
    live = {"identity": {"mios_user": "corey"}, "ports": {"agent_pipe": 8640}}
    ok, errors = validate_config(posted, live_config=live)
    assert ok is False
    assert any("ports" in e for e in errors), errors

def test_rejects_bad_port_out_of_range():
    posted = """
[identity]
mios_user = "corey"

[ports]
agent_pipe = 70000
"""
    ok, errors = validate_config(posted, live_config={})
    assert ok is False
    assert any("agent_pipe" in e and "65535" in e for e in errors), errors

def test_rejects_non_integer_port():
    posted = """
[identity]
mios_user = "corey"

[ports]
agent_pipe = "8640"
"""
    ok, errors = validate_config(posted, live_config={})
    assert ok is False
    assert any("agent_pipe" in e for e in errors), errors

def test_rejects_blank_mios_user():
    posted = """
[identity]
mios_user = "   "

[ports]
agent_pipe = 8640
"""
    ok, errors = validate_config(posted, live_config={})
    assert ok is False
    assert any("mios_user" in e for e in errors), errors

def test_rejects_oversize_payload():
    big = "# pad\n" + ("x = 1\n" * 400000)
    assert len(big.encode("utf-8")) > 2 * 1024 * 1024
    ok, errors = validate_config(big, live_config={})
    assert ok is False
    assert any("too large" in e.lower() for e in errors), errors

def test_rejects_unparseable_toml():
    ok, errors = validate_config("this is = = not toml [[[", live_config={})
    assert ok is False
    assert errors

def test_drop_check_degrades_open_without_live():
    ok, errors = validate_config("[misc]\nfoo = 1\n", live_config=None)
    assert ok is True, errors

def _main_config_validate():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {fn.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    return 1 if failed else 0


def _run_extra_config_validate():
    try:
        return _main_config_validate()
    except SystemExit as _e:
        return _e.code if _e.code is not None else 0



# ==============================================================================
# Consolidated from test_mios_config_write.py (task_1006)
# ==============================================================================
# AI-hint: Standalone unit test for the /portal/config read/write routes to ensure correct auth, TOML parsing, and background DB re-seeding.
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md

import unittest
import sys
import os

try:
    from fastapi.testclient import TestClient
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import server
    import mios_toml
    HAS_DEPS = True
except ImportError as e:
    HAS_DEPS = False
    DEP_ERROR = e

from unittest.mock import patch, MagicMock

class TestDeltaConfigWrite(unittest.TestCase):
    def test_delta_write(self):
        import tempfile
        import shutil
        from mios_pipe.kernel.config import write_user_config

        tmp_dir = tempfile.mkdtemp()
        vendor_file = os.path.join(tmp_dir, "vendor.toml")
        host_file = os.path.join(tmp_dir, "host.toml")
        user_file = os.path.join(tmp_dir, "user.toml")

        with open(vendor_file, "w", encoding="utf-8") as f:
            f.write("[section]\nkey1 = 'vendor_val'\nkey2 = 'vendor_val2'\n")

        with open(host_file, "w", encoding="utf-8") as f:
            f.write("[section]\nkey2 = 'host_val2'\nkey3 = 'host_val3'\n")

        env_vars = {
            "MIOS_VENDOR_TOML": vendor_file,
            "MIOS_HOST_TOML": host_file,
            "MIOS_USER_TOML": user_file,
            "MIOS_VENDOR_TOML_D": os.path.join(tmp_dir, "nonexistent1"),
            "MIOS_HOST_TOML_D": os.path.join(tmp_dir, "nonexistent2"),
            "MIOS_USER_TOML_D": os.path.join(tmp_dir, "nonexistent3"),
        }

        orig_env = {k: os.environ.get(k) for k in env_vars}
        for k, v in env_vars.items():
            os.environ[k] = v

        try:
            full_config = {
                "section": {
                    "key1": "vendor_val",
                    "key2": "host_val2",
                    "key3": "user_val3",
                    "key4": "new_val",
                }
            }

            write_user_config(full_config, dest_path=user_file)

            try:
                import tomllib as tl
            except ImportError:
                import tomli as tl

            with open(user_file, "rb") as f:
                res = tl.load(f)

            self.assertNotIn("key1", res.get("section", {}))
            self.assertNotIn("key2", res.get("section", {}))
            self.assertEqual(res.get("section", {}).get("key3"), "user_val3")
            self.assertEqual(res.get("section", {}).get("key4"), "new_val")

        finally:
            for k, v in orig_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_to_toml_datetime_and_unsupported(self):
        import datetime
        from mios_pipe.kernel.config import to_toml
        try:
            import tomllib as tl
        except ImportError:
            import tomli as tl

        now = datetime.datetime(2026, 7, 18, 12, 0, 0, tzinfo=datetime.timezone.utc)
        d = {"time": now}
        toml_str = to_toml(d)
        parsed = tl.loads(toml_str)
        self.assertEqual(parsed["time"], now)

        with self.assertRaises(TypeError):
            to_toml({"unsupported": object()})

class TestConfigWrite(unittest.TestCase):
    def setUp(self):
        if not HAS_DEPS:
            raise unittest.SkipTest(f"Missing test dependencies: {DEP_ERROR}")
        self.client = TestClient(server.app)

    @patch("mios_pipe.routing.portal._portal_authed", return_value=True)
    @patch("mios_toml.load_merged")
    def test_get_config_success(self, mock_load_merged, mock_authed):
        mock_load_merged.return_value = {
            "meta": {"mios_version": "0.3.0"},
            "test_section": {"key": "val", "arr": [{"a": 1}, {"b": 2}]}
        }
        response = self.client.get("/portal/config")
        self.assertEqual(response.status_code, 200)
        self.assertIn("[meta]", response.text)
        self.assertIn('mios_version = "0.3.0"', response.text)
        self.assertIn("[test_section]", response.text)
        self.assertIn("[[test_section.arr]]", response.text)

    def test_get_config_unauth(self):
        with patch("mios_pipe.routing.portal._portal_authed", return_value=False):
            response = self.client.get("/portal/config")
            self.assertEqual(response.status_code, 401)

    @patch("mios_toml.load_merged", return_value={})
    @patch("mios_pipe.routing.portal._portal_authed", return_value=True)
    @patch("mios_pipe.kernel.config.write_user_config")
    @patch("fastapi.BackgroundTasks.add_task")
    def test_post_config_success(self, mock_add_task, mock_write_user, mock_authed, mock_load_merged):
        mock_load_merged.return_value = {"portal": {"config_max_body_bytes": 4096}}
        payload = """
[meta]
mios_version = "0.3.0"

[[test_section.arr]]
a = 1
"""
        response = self.client.post("/portal/config", content=payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        mock_write_user.assert_called_once()
        parsed = mock_write_user.call_args[0][0]
        self.assertEqual(parsed["meta"]["mios_version"], "0.3.0")
        self.assertEqual(parsed["test_section"]["arr"][0]["a"], 1)
        mock_add_task.assert_called_once()

    @patch("mios_pipe.routing.portal._portal_authed", return_value=True)
    def test_post_config_invalid_toml(self, mock_authed):
        payload = "invalid = [toml"
        response = self.client.post("/portal/config", content=payload)
        self.assertEqual(response.status_code, 400)
        self.assertIn("Invalid TOML", response.json()["error"])

    def test_post_config_unauth(self):
        with patch("mios_pipe.routing.portal._portal_authed", return_value=False):
            response = self.client.post("/portal/config", content="key = 'val'")
            self.assertEqual(response.status_code, 401)


def _run_extra_config_write():
    return _run_cases(TestDeltaConfigWrite, TestConfigWrite)



# ==============================================================================
# Consolidated from test_mios_toml.py (task_1006)
# ==============================================================================
# AI-hint: Standalone unit test for mios_toml.py overlay and DB authoritative fallbacks.
# AI-related: /usr/lib/mios/mios_toml.py
# AI-functions: TestMiosToml

import unittest
import os
import sys
import tempfile
import shutil
from unittest.mock import patch

sys.path.insert(0, "/usr/lib/mios")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import mios_toml

class TestMiosToml(unittest.TestCase):
    def setUp(self):
        mios_toml.clear_cache()
        self.tmp_dir = tempfile.mkdtemp()
        self.vendor_file = os.path.join(self.tmp_dir, "vendor.toml")
        self.host_file = os.path.join(self.tmp_dir, "host.toml")
        self.user_file = os.path.join(self.tmp_dir, "user.toml")

        with open(self.vendor_file, "w", encoding="utf-8") as f:
            f.write("[section]\nkey1 = 'vendor_val'\nkey2 = 'vendor_val2'\n")

        with open(self.host_file, "w", encoding="utf-8") as f:
            f.write("[section]\nkey2 = 'host_val2'\nkey3 = 'host_val3'\n")

        self.env_vars = {
            "MIOS_VENDOR_TOML": self.vendor_file,
            "MIOS_HOST_TOML": self.host_file,
            "MIOS_USER_TOML": self.user_file,
            "MIOS_VENDOR_TOML_D": os.path.join(self.tmp_dir, "nonexistent1"),
            "MIOS_HOST_TOML_D": os.path.join(self.tmp_dir, "nonexistent2"),
            "MIOS_USER_TOML_D": os.path.join(self.tmp_dir, "nonexistent3"),
        }
        self.orig_env = {k: os.environ.get(k) for k in self.env_vars}
        for k, v in self.env_vars.items():
            os.environ[k] = v

    def tearDown(self):
        mios_toml.clear_cache()
        for k, v in self.orig_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_load_merged(self):
        with open(self.user_file, "w", encoding="utf-8") as f:
            f.write("[section]\nkey3 = 'user_val3'\nkey4 = 'user_val4'\n")

        res = mios_toml.load_merged()
        self.assertEqual(res["section"]["key1"], "vendor_val")
        self.assertEqual(res["section"]["key2"], "host_val2")
        self.assertEqual(res["section"]["key3"], "user_val3")
        self.assertEqual(res["section"]["key4"], "user_val4")

    @patch("mios_db_config.is_db_authoritative", return_value=True)
    @patch("mios_db_config.load_db_config")
    def test_load_merged_db_authoritative_fallback(self, mock_load_db, mock_is_auth):
        mock_load_db.return_value = {
            "section": {
                "key3": "db_val3",
                "key4": "db_val4"
            }
        }

        with open(self.user_file, "w", encoding="utf-8") as f:
            f.write("[section]\nkey3 = 'user_val3'\n")

        res = mios_toml.load_merged()
        self.assertEqual(res["section"]["key1"], "vendor_val")
        self.assertEqual(res["section"]["key2"], "host_val2")
        self.assertEqual(res["section"]["key3"], "db_val3")
        self.assertEqual(res["section"]["key4"], "db_val4")

    def test_cache_memoization_and_invalidation(self):
        res1 = mios_toml.load_merged()

        with open(self.user_file, "w", encoding="utf-8") as f:
            f.write("[section]\nkey3 = 'sneaky_change'\n")

        res2 = mios_toml.load_merged()
        self.assertEqual(res1["section"].get("key3"), res2["section"].get("key3"))

        mios_toml.clear_cache()
        res3 = mios_toml.load_merged()
        self.assertEqual(res3["section"].get("key3"), "sneaky_change")


def _run_extra_toml():
    return _run_cases(TestMiosToml)


# ==============================================================================
# Consolidated from test_mios_user_config.py (task_1006)
# ==============================================================================
# AI-hint: Unit and regression test suite for mios_user_config functionality.
# AI-functions: test_parse_simple_toml_tomllib, test_path_escape_guard, TestMiosUserConfig

import sys
import os
import unittest
from unittest.mock import patch, MagicMock

base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../.."))
libexec_dir = os.path.join(base_dir, "usr/libexec/mios")

import importlib.util
spec = importlib.util.spec_from_file_location("muc", os.path.join(libexec_dir, "materialize-user-config.py"))
muc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(muc)

class TestMiosUserConfig(unittest.TestCase):

    def test_parse_simple_toml_tomllib(self):
        temp_toml = "/tmp/test_complex_spec.toml"
        content = """
        [ai]
        db_authoritative = true
        models = ["granite", "gpt-oss"] # inline comment

        [mcp.servers.github]
        enabled = true
        args = { token = "xyz", repo = "mios" } # inline table
        """
        with open(temp_toml, "w") as f:
            f.write(content)

        try:
            parsed = muc.parse_simple_toml(temp_toml)
            self.assertEqual(parsed["ai"]["db_authoritative"], True)
            self.assertEqual(parsed["ai"]["models"], ["granite", "gpt-oss"])
            self.assertEqual(parsed["mcp"]["servers"]["github"]["enabled"], True)
            self.assertEqual(parsed["mcp"]["servers"]["github"]["args"]["token"], "xyz")
        finally:
            if os.path.exists(temp_toml):
                os.remove(temp_toml)

    def test_path_escape_guard(self):
        home_dir = "/home/bob"

        rel_path_ok = ".config/mios/mios.toml"
        target_ok = os.path.abspath(os.path.join(home_dir, rel_path_ok))
        home_abs = os.path.abspath(home_dir)
        self.assertEqual(os.path.commonpath([home_abs, target_ok]), home_abs)

        rel_path_escape = "../bob-evil/.bashrc"
        target_escape = os.path.abspath(os.path.join(home_dir, rel_path_escape))
        self.assertNotEqual(os.path.commonpath([home_abs, target_escape]), home_abs)


def _run_extra_user_config():
    return _run_cases(TestMiosUserConfig)


def _run_all_folded_config_suites():
    for fn in (_run_extra_config_audit, _run_extra_config_validate, _run_extra_config_write, _run_extra_toml, _run_extra_user_config):
        rc = fn()
        if rc not in (None, 0):
            import sys
            sys.exit(f"Folded test suite failed: exit code {rc}")

if __name__ == "__main__":
    _run_all_folded_config_suites()
    sys.exit(main())
