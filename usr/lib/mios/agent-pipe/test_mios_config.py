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

    def test_to_toml_round_trips_nested_arrays_and_the_shipped_ssot(self):
        from mios_pipe.kernel.config import to_toml
        nested = {"a": {"rows": [[1, 2], ["x", {"k": "v", "n": [1]}]], "q": 'a"b\\c\nd'}}
        self.assertEqual(tl.loads(to_toml(nested)), nested)
        vendor = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..",
                              "share", "mios", "mios.toml")
        with open(vendor, "rb") as fh:
            shipped = tl.load(fh)
        self.assertEqual(tl.loads(to_toml(shipped)), shipped, "GET /portal/config cannot serve the SSOT")

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


try:
    import tomllib as tl
except ImportError:
    import tomli as tl  # type: ignore


class TestRestoreRedacted(unittest.TestCase):
    """restore_redacted: the redaction marker means "unchanged" (C3)."""

    def test_marker_takes_the_live_value_or_is_dropped(self):
        from mios_pipe.kernel.config import REDACTED, restore_redacted
        self.assertEqual(REDACTED, "<redacted>")
        live = {"portal": {"password": "live-pw", "user": "op"},
                "remotes": [{"name": "a", "github_token": "ghp_a"},
                            {"name": "b", "github_token": "ghp_b"}],
                "env": {"list": ["x"]}}
        posted = {"portal": {"password": REDACTED, "secret": REDACTED, "user": "edited"},
                  "remotes": [{"name": "a", "github_token": REDACTED},
                              {"name": "b", "github_token": REDACTED},
                              {"name": "c", "github_token": REDACTED}],
                  "env": {"list": [REDACTED, REDACTED, "kept"]},
                  "ai": {"api_key": REDACTED}}
        self.assertEqual(restore_redacted(posted, live), {
            "portal": {"password": "live-pw", "user": "edited"},
            "remotes": [{"name": "a", "github_token": "ghp_a"},
                        {"name": "b", "github_token": "ghp_b"},
                        {"name": "c"}],
            "env": {"list": ["x", "kept"]},
            "ai": {}})
        self.assertEqual(restore_redacted({"ai": {"api_key": REDACTED, "endpoint": "e"}}, None),
                         {"ai": {"endpoint": "e"}})
        from mios_pipe.kernel.config import has_redacted
        self.assertTrue(has_redacted(posted))
        self.assertFalse(has_redacted(restore_redacted(posted, live)))
        self.assertEqual(posted["portal"]["password"], REDACTED, "the input is not mutated")


class TestRedactSecrets(unittest.TestCase):
    """redact_secrets mirrors miosd's redact_secrets (ruling 3)."""

    def test_fixed_and_secret_keys_paths_at_any_depth(self):
        from mios_pipe.kernel.config import REDACTED, redact_secrets
        merged = {"portal": {"password": "", "secret": "s", "user": "op"},
                  "ai": {"api_key": "k", "endpoint": "e"},
                  "security": {"secret_keys": {"keys": ["MIOS_GITHUB_TOKEN", "Webhook_Secret", 7]}},
                  "a": {"b": {"c": {"github_token": "t1", "GitHub_Token": "t2", "mios_github_token": "n"}}},
                  "remotes": [{"name": "o", "GITHUB_TOKEN": "t3"}, [{"webhook_secret": "t4"}]],
                  "svc": {"webhook_secret": {"nested": "whole-table"}, "keys": ["github_token"]},
                  "list": ["github_token"]}
        out = redact_secrets(merged)
        self.assertEqual(out, {
            "portal": {"password": REDACTED, "secret": REDACTED, "user": "op"},
            "ai": {"api_key": REDACTED, "endpoint": "e"},
            "security": {"secret_keys": {"keys": ["MIOS_GITHUB_TOKEN", "Webhook_Secret", 7]}},
            "a": {"b": {"c": {"github_token": REDACTED, "GitHub_Token": REDACTED,
                              "mios_github_token": REDACTED}}},
            "remotes": [{"name": "o", "GITHUB_TOKEN": REDACTED}, [{"webhook_secret": REDACTED}]],
            "svc": {"webhook_secret": REDACTED, "keys": ["github_token"]},
            "list": ["github_token"]})
        self.assertEqual(merged["portal"]["secret"], "s", "the input is not mutated")
        self.assertEqual(redact_secrets({"x": {"github_token": "t"}, "ai": {"endpoint": "e"}}),
                         {"x": {"github_token": "t"}, "ai": {"endpoint": "e"}},
                         "without [security.secret_keys] only the fixed paths are secrets")


class TestPortalConfigEnvelopeAndRedaction(unittest.TestCase):
    """POST /portal/config end to end on temp vendor/host/user tiers: the lossless
    {"toml": ...} JSON envelope, raw TOML, and an echoed redaction marker (C3).
    HOME, XDG_CONFIG_HOME and MIOS_USER_TOML all point at the temp user tier, so
    no live tier is read or written."""

    VENDOR = ('[identity]\nmios_user = "vendor-user"\nusername = "user"\n\n'
              '[ports]\nagent_pipe = 8640\n\n'
              '[portal]\nconfig_max_body_bytes = 2097152\npassword = ""\nsecret = ""\n\n'
              '[ai]\napi_key = ""\nendpoint = "vendor"\n\n'
              '[security.secret_keys]\nkeys = ["MIOS_GITHUB_TOKEN", "Webhook_Secret"]\n\n'
              '[deep.unknown.nested]\ngithub_token = "vendor-deep-token"\nlabel = "kept"\n'
              'curve = [[0, 30], [80, {at = 100}]]\n\n'
              '[svc.webhook_secret]\nvalue = "vendor-table-secret"\n\n'
              '[[remotes]]\nname = "origin"\nGITHUB_TOKEN = "vendor-array-token"\n')
    # Every path the native portal serves as <redacted> for VENDOR + HOST.
    SECRET_PATHS = {("portal", "password"), ("portal", "secret"), ("ai", "api_key"),
                    ("deep", "unknown", "nested", "github_token"), ("svc", "webhook_secret"),
                    ("remotes", 0, "GITHUB_TOKEN")}
    SECRETS = ("host-tier-cookie-secret", "user-tier-portal-pw", "user-tier-api-key",
               "vendor-deep-token", "vendor-table-secret", "vendor-array-token")
    HOST = '[portal]\nsecret = "host-tier-cookie-secret"\n'
    USER = '[portal]\npassword = "user-tier-portal-pw"\n\n[ai]\napi_key = "user-tier-api-key"\n'
    BROKEN = '[portal\npassword = \n'

    def setUp(self):
        if not HAS_DEPS:
            raise unittest.SkipTest(f"Missing test dependencies: {DEP_ERROR}")
        import shutil
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.user = os.path.join(self.tmp, ".config", "mios", "mios.toml")
        for name, text in (("vendor.toml", self.VENDOR), ("host.toml", self.HOST)):
            with open(os.path.join(self.tmp, name), "w", encoding="utf-8") as fh:
                fh.write(text)
        env = patch.dict(os.environ, {
            "HOME": self.tmp, "XDG_CONFIG_HOME": os.path.join(self.tmp, ".config"),
            "MIOS_VENDOR_TOML": os.path.join(self.tmp, "vendor.toml"),
            "MIOS_VENDOR_TOML_D": os.path.join(self.tmp, "vendor.d"),
            "MIOS_HOST_TOML": os.path.join(self.tmp, "host.toml"),
            "MIOS_HOST_TOML_D": os.path.join(self.tmp, "host.d"),
            "MIOS_USER_TOML": self.user,
            "MIOS_USER_TOML_D": os.path.join(self.tmp, "user.d"),
            "MIOS_RESOLVER_NATIVE": "0", "MIOS_DB_AUTHORITATIVE": "0"})
        env.start()
        self.addCleanup(env.stop)
        for var in ("MIOS_TOML", "MIOS_TOML_ROOT"):
            os.environ.pop(var, None)
        for p in (patch("mios_db_config.is_db_authoritative", return_value=False),
                  patch("mios_pipe.routing.portal._portal_authed", return_value=True),
                  patch("fastapi.BackgroundTasks.add_task")):
            p.start()
            self.addCleanup(p.stop)
        mios_toml.clear_cache()
        self.addCleanup(mios_toml.clear_cache)
        self.client = TestClient(server.app)

    def tier(self):
        try:
            with open(self.user, encoding="utf-8") as fh:
                return fh.read()
        except FileNotFoundError:
            return None

    def plant_user_tier(self):
        os.makedirs(os.path.dirname(self.user), exist_ok=True)
        with open(self.user, "w", encoding="utf-8") as fh:
            fh.write(self.USER)
        mios_toml.clear_cache()

    def served(self):
        response = self.client.get("/portal/config")
        self.assertEqual(response.status_code, 200)
        return response.text

    def redacted_echo(self):
        """The redacted GET itself: what a configurator save echoes."""
        served = self.served()
        self.assertIn('"<redacted>"', served)
        return served

    def post(self, body, content_type):
        return self.client.post("/portal/config", content=body,
                                headers={"Content-Type": content_type})

    def test_json_envelope_saves_the_toml_text(self):
        edited = self.served().replace('username = "user"', 'username = "envelope-op"')
        response = self.post(json.dumps({"toml": edited}), "application/json; charset=utf-8")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(tl.loads(self.tier()), {"identity": {"username": "envelope-op"}})

    def test_raw_toml_is_still_accepted(self):
        edited = self.served().replace('username = "user"', 'username = "raw-op"')
        for content_type in ("application/toml", "text/plain"):
            response = self.post(edited, content_type)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(tl.loads(self.tier()), {"identity": {"username": "raw-op"}})

    def test_a_bad_envelope_writes_nothing(self):
        self.plant_user_tier()
        served = self.served()
        for body in ("not json", json.dumps({"config": tl.loads(served)}), json.dumps({"toml": 5}),
                     json.dumps(["toml", served])):
            response = self.post(body, "application/json")
            self.assertEqual(response.status_code, 400, body[:60])
            self.assertIn("envelope", response.json()["error"])
        response = self.post(json.dumps({"toml": "[broken_section\nx = 1\n"}), "application/json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Invalid TOML", response.json()["error"])
        self.assertEqual(self.tier(), self.USER)

    def test_an_echoed_marker_keeps_the_user_tier_secrets(self):
        self.plant_user_tier()
        edited = self.redacted_echo().replace('username = "user"', 'username = "echo-op"')
        response = self.post(json.dumps({"toml": edited}), "application/json")
        self.assertEqual(response.status_code, 200, response.text)
        text = self.tier()
        self.assertNotIn("<redacted>", text)
        self.assertNotIn("host-tier-cookie-secret", text, "a lower-tier secret was copied up")
        self.assertEqual(tl.loads(text), {"identity": {"username": "echo-op"},
                                          "portal": {"password": "user-tier-portal-pw"},
                                          "ai": {"api_key": "user-tier-api-key"}})
        live = mios_toml.load_merged()
        self.assertEqual((live["portal"]["password"], live["portal"]["secret"], live["ai"]["api_key"]),
                         ("user-tier-portal-pw", "host-tier-cookie-secret", "user-tier-api-key"))

    def broken_user_d(self):
        """A malformed user.d override beside the valid user tier."""
        frag = os.path.join(self.tmp, "user.d", "10-broken.toml")
        os.makedirs(os.path.dirname(frag), exist_ok=True)
        with open(frag, "w", encoding="utf-8") as fh:
            fh.write(self.BROKEN)
        mios_toml.clear_cache()
        return frag

    def assert_refused_and_kept(self, response):
        self.assertEqual(response.status_code, 500, response.text)
        self.assertIn("nothing was written", response.json()["error"])
        self.assertEqual(self.tier(), self.USER)
        self.assertFalse(os.path.exists(self.user + ".tmp"))

    def assert_saved_and_kept(self, response):
        self.assertEqual(response.status_code, 200, response.text)
        saved = tl.loads(self.tier())
        self.assertEqual((saved["identity"], saved["portal"], saved["ai"]),
                         ({"username": "echo-op"}, {"password": "user-tier-portal-pw"},
                          {"api_key": "user-tier-api-key"}))

    def test_an_echo_against_a_malformed_override_is_refused(self):
        self.plant_user_tier()
        body = json.dumps({"toml": self.redacted_echo().replace('username = "user"',
                                                                'username = "echo-op"')})
        frag = self.broken_user_d()
        self.assert_refused_and_kept(self.post(body, "application/json"))
        with open(frag, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), self.BROKEN)
        # Planted-error control: the same echo saves once the override parses.
        os.remove(frag)
        mios_toml.clear_cache()
        self.assert_saved_and_kept(self.post(body, "application/json"))

    def test_an_echo_whose_live_config_did_not_load_is_refused(self):
        self.plant_user_tier()
        body = json.dumps({"toml": self.redacted_echo().replace('username = "user"',
                                                                'username = "echo-op"')})
        real, calls = mios_toml.load_merged, []

        def first_load_fails(*args, **kwargs):
            calls.append(args)
            if len(calls) == 1:
                raise RuntimeError("live config unloadable")
            return real(*args, **kwargs)

        # Only the handler's live_config load fails; the size-cap lookup after it resolves.
        with patch("mios_toml.load_merged", side_effect=first_load_fails):
            self.assert_refused_and_kept(self.post(body, "application/json"))
        self.assertGreaterEqual(len(calls), 2, "the refusal came before the live load failed")
        # Planted-error control: the same echo saves once the live config loads.
        self.assert_saved_and_kept(self.post(body, "application/json"))

    def test_unresolved_lower_tiers_refuse_the_write(self):
        from mios_pipe.kernel.config import REDACTED, TiersUnresolved, write_user_config
        self.plant_user_tier()
        live = mios_toml.load_merged()
        cfg = {"identity": {"username": "echo-op"}, "portal": {"password": REDACTED, "secret": REDACTED},
               "ai": {"api_key": REDACTED}}
        with patch("mios_toml.derive_ports", side_effect=RuntimeError("ports did not derive")):
            with self.assertRaises(TiersUnresolved):
                write_user_config(cfg, dest_path=self.user, live_config=live)
        self.assertEqual(self.tier(), self.USER, "an empty base copied the host-tier secret up")
        # Planted-error control: the same write lands once the lower tiers resolve.
        write_user_config(cfg, dest_path=self.user, live_config=live)
        self.assertEqual(tl.loads(self.tier()), {"identity": {"username": "echo-op"},
                                                 "portal": {"password": "user-tier-portal-pw"},
                                                 "ai": {"api_key": "user-tier-api-key"}})

    def marker_paths(self, value, at=()):
        """Each path whose value is the redaction marker, and every other leaf."""
        from mios_pipe.kernel.config import REDACTED
        if value == REDACTED:
            return {at}, {}
        items = value.items() if isinstance(value, dict) else enumerate(value) if isinstance(value, list) else None
        if items is None:
            return set(), {at: value}
        marks, leaves = set(), {}
        for k, v in items:
            m, lv = self.marker_paths(v, at + (k,))
            marks |= m
            leaves.update(lv)
        return marks, leaves

    def test_get_requires_auth_and_redacts_like_the_native_portal(self):
        self.plant_user_tier()
        with patch("mios_pipe.routing.portal._portal_authed", return_value=False):
            denied = self.client.get("/portal/config")
        self.assertEqual(denied.status_code, 401)
        self.assertNotIn("[identity]", denied.text)
        served = self.served()
        for secret in self.SECRETS:
            self.assertNotIn(secret, served, "an authenticated GET is still not a reveal")
        marks, leaves = self.marker_paths(tl.loads(served))
        self.assertEqual(marks, self.SECRET_PATHS)
        # Schema: every non-secret leaf is served unchanged, and no other is added or lost.
        live_marks, live_leaves = self.marker_paths(mios_toml.load_merged())
        self.assertEqual(live_marks, set())
        hidden = {p for p in live_leaves if any(p[:len(s)] == s for s in self.SECRET_PATHS)}
        self.assertEqual(leaves, {p: v for p, v in live_leaves.items() if p not in hidden})
        for path, value in ((("identity", "mios_user"), "vendor-user"), (("ports", "agent_pipe"), 8640),
                            (("portal", "config_max_body_bytes"), 2097152), (("ai", "endpoint"), "vendor"),
                            (("deep", "unknown", "nested", "label"), "kept"), (("remotes", 0, "name"), "origin"),
                            (("deep", "unknown", "nested", "curve", 1, 1, "at"), 100),
                            (("security", "secret_keys", "keys", 0), "MIOS_GITHUB_TOKEN")):
            self.assertEqual(leaves.get(path), value, path)

    def test_an_orphan_marker_is_dropped(self):
        orphan = (self.redacted_echo()
                  + '\n[deep.unknown.other]\ngithub_token = "<redacted>"\nnote = "new"\n'
                  + '\n[orphan]\napi_key = "<redacted>"\nlist = ["<redacted>", "kept"]\n')
        response = self.post(json.dumps({"toml": orphan}), "application/json")
        self.assertEqual(response.status_code, 200, response.text)
        text = self.tier()
        self.assertNotIn("<redacted>", text)
        for secret in self.SECRETS:
            self.assertNotIn(secret, text, "a lower-tier secret was copied up")
        self.assertEqual(tl.loads(text), {"deep": {"unknown": {"other": {"note": "new"}}},
                                          "orphan": {"list": ["kept"]}})

    def test_an_echo_keeps_each_secret_in_its_own_layer(self):
        self.plant_user_tier()
        frag = os.path.join(self.tmp, "user.d", "20-own.toml")
        os.makedirs(os.path.dirname(frag), exist_ok=True)
        with open(frag, "w", encoding="utf-8") as fh:
            fh.write('[portal]\npassword = "user-d-portal-pw"\nsecret = "user-d-cookie-secret"\n')
        mios_toml.clear_cache()
        edited = self.redacted_echo().replace('username = "user"', 'username = "echo-op"')
        response = self.post(json.dumps({"toml": edited}), "application/json")
        self.assertEqual(response.status_code, 200, response.text)
        text = self.tier()
        for foreign in ("user-d-portal-pw", "user-d-cookie-secret", "<redacted>") + self.SECRETS[:1]:
            self.assertNotIn(foreign, text, "user.d and lower-tier secrets stay in their own layers")
        self.assert_saved_and_kept(response)
        live = mios_toml.load_merged()
        self.assertEqual((live["portal"]["password"], live["portal"]["secret"]),
                         ("user-d-portal-pw", "user-d-cookie-secret"))

    def test_the_default_destination_is_the_resolved_user_tier(self):
        from mios_pipe.kernel.config import write_user_config
        elsewhere = os.path.join(self.tmp, "elsewhere", "mios.toml")
        with patch.dict(os.environ, {"MIOS_USER_TOML": elsewhere}):
            write_user_config({"identity": {"username": "moved-op"}}, live_config={})
        self.assertIsNone(self.tier(), "the write went to ~/.config, not the resolved user tier")
        with open(elsewhere, encoding="utf-8") as fh:
            self.assertEqual(tl.loads(fh.read()), {"identity": {"username": "moved-op"}})

    def test_a_no_edit_echo_writes_nothing_beyond_what_changed(self):
        for content_type in ("application/json", "application/toml"):
            echo = self.redacted_echo()
            body = json.dumps({"toml": echo}) if content_type == "application/json" else echo
            response = self.post(body, content_type)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(self.tier(), "", content_type)


def _run_extra_config_write():
    return _run_cases(TestDeltaConfigWrite, TestConfigWrite, TestRestoreRedacted,
                      TestRedactSecrets, TestPortalConfigEnvelopeAndRedaction)



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
