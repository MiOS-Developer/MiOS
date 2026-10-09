# AI-hint: Stdlib unit test for mios_verbcatalog -- the verb/recipe catalog loader + 3-projection SSOT source.
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md
"""Offline unit tests for ``mios_verbcatalog`` (no network, no DB)."""

import os
import tempfile
import unittest

import mios_verbcatalog as VC

SYNTH_TOML = """
[verbs.open_app]
section = "Window / app launch"
sig = "name"
desc = "Open an application by name"
tier = "common"
permission = "write"
model_name = "launch_application"
hidden_aliases = ["open_application", "start_app"]
[verbs.open_app.params.name]
type = "string"
desc = "application name"
aliases = ["query", "title", "app", "target", "program", "path", "file", "binary", "exe"]

[verbs.recall]
section = "Memory"
sig = "query"
desc = "Recall a stored fact"
tier = "common"
permission = "read"
[verbs.recall.params.query]
type = "string"
desc = "what to recall"
default = ""
[verbs.recall.params.limit]
type = "integer"
default = 30
aliases = ["n", "top_k"]

[verbs.ui_button]
label = "Build"

[recipes.toast]
description = "Show a desktop toast"
args = ["message"]
permission = "read"
"""

class VerbCatalogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.NamedTemporaryFile(
            "w", suffix=".toml", delete=False, encoding="utf-8")
        cls._tmp.write(SYNTH_TOML)
        cls._tmp.close()
        os.environ["MIOS_TOML"] = cls._tmp.name
        VC.configure(CATALOG_FAIL_MODE="warn")
        VC._DB_UNREACHABLE = True
        cls.cat = VC._load_verb_catalog()
        cls.model_map = VC._build_model_name_map(cls.cat)
        VC.configure(_VERB_CATALOG=cls.cat, _MODEL_NAME_TO_VERB=cls.model_map)

    @classmethod
    def tearDownClass(cls):
        try:
            os.unlink(cls._tmp.name)
        except OSError:
            pass

    def test_load_parses_only_agent_verbs(self):
        self.assertIn("open_app", self.cat)
        self.assertIn("recall", self.cat)
        self.assertNotIn("ui_button", self.cat)
        self.assertEqual(self.cat["open_app"]["permission"], "write")
        self.assertEqual(self.cat["open_app"]["model_name"], "launch_application")
        self.assertEqual(
            self.cat["open_app"]["hidden_aliases"], ["open_application", "start_app"])

    def test_verb_to_openai_projection(self):
        tool = VC._verb_to_openai_tool("open_app", self.cat["open_app"])
        self.assertEqual(tool["type"], "function")
        self.assertEqual(tool["function"]["name"], "launch_application")
        self.assertEqual(tool["x-mios-verb"], "open_app")
        self.assertEqual(tool["x-mios-permission"], "write")
        self.assertTrue(tool["function"]["strict"])
        params = tool["function"]["parameters"]
        self.assertEqual(params["additionalProperties"], False)
        self.assertIn("name", params["properties"])
        self.assertIn("name", params["required"])
        self.assertEqual(params["properties"]["name"]["type"], "string")

    def test_verb_to_openai_optional_param_nullable(self):
        tool = VC._verb_to_openai_tool("recall", self.cat["recall"])
        spec = tool["function"]["parameters"]["properties"]["limit"]
        self.assertEqual(spec["type"], ["integer", "null"])
        self.assertEqual(tool["function"]["name"], "recall")

    def test_recipe_projection(self):
        rec = VC._load_recipe_catalog()
        self.assertIn("toast", rec)
        tool = VC._recipe_to_openai_tool("toast", rec["toast"])
        self.assertEqual(tool["function"]["name"], "mios_recipe__toast")
        self.assertEqual(tool["x-mios-recipe"], "toast")
        props = tool["function"]["parameters"]["properties"]
        self.assertIn("message", props)
        self.assertIn("os", props)            # injected OS selector
        self.assertEqual(props["message"]["type"], ["string", "null"])
        rich_cfg = {
            "description": "Rich toast",
            "args": {
                "message": {
                    "type": "string",
                    "description": "the message"
                },
                "duration": {
                    "type": "integer",
                    "description": "duration in seconds"
                }
            },
            "permission": "read"
        }
        tool_rich = VC._recipe_to_openai_tool("toast_rich", rich_cfg)
        self.assertEqual(tool_rich["function"]["name"], "mios_recipe__toast_rich")
        props_rich = tool_rich["function"]["parameters"]["properties"]
        self.assertIn("message", props_rich)
        self.assertIn("duration", props_rich)
        self.assertIn("os", props_rich)
        self.assertEqual(props_rich["message"]["type"], ["string", "null"])
        self.assertEqual(props_rich["message"]["description"], "the message")
        self.assertEqual(props_rich["duration"]["type"], ["integer", "null"])
        self.assertEqual(props_rich["duration"]["description"], "duration in seconds")
        self.assertTrue(tool_rich["function"]["strict"])

    def test_render_verb_catalog_prose(self):
        prose = VC._render_verb_catalog(self.cat)
        self.assertIn("open_app", prose)
        self.assertIn("Window / app launch", prose)
        self.assertIn("Memory", prose)

    def test_render_recipe_catalog_prose(self):
        rec = VC._load_recipe_catalog()
        prose = VC._render_recipe_catalog(rec)
        self.assertIn("toast", prose)

    def test_model_name_and_alias_resolution(self):
        self.assertEqual(self.model_map["launch_application"], "open_app")
        self.assertEqual(self.model_map["open_application"], "open_app")
        self.assertEqual(self.model_map["start_app"], "open_app")
        self.assertEqual(VC._resolve_verb_key("launch_application"), "open_app")
        self.assertEqual(VC._resolve_verb_key("open_app"), "open_app")
        self.assertEqual(VC._resolve_verb_key("nope"), "nope")
        self.assertEqual(VC._resolve_verb_key(""), "")

    def test_arg_synonyms_projection(self):
        syn = VC._verb_arg_synonyms_from_catalog(self.cat)
        self.assertEqual(syn["open_app"]["name"], ["app", "binary", "exe", "file", "path", "program", "query", "target", "title"])
        self.assertEqual(VC._load_verb_arg_synonyms(), syn)

    def test_identity_answer_from_live_catalog(self):
        ans = VC._identity_answer()
        self.assertTrue(ans)
        self.assertIn("MiOS-Agent", ans)



# ==============================================================================
# Consolidated from test_mios_build_catalog.py (task_1006)
# ==============================================================================
# AI-hint: Unit and regression test suite for mios_build_catalog functionality.
# AI-functions: setUpModule, test_seeding_and_materializing, test_materialization, mock_open_impl, write_impl, TestMiosBuildCatalog

import unittest
from unittest.mock import patch, AsyncMock, MagicMock, mock_open
import os
import json
import sys

sys.path.insert(0, "/mnt/c/MiOS/usr/libexec/mios")

def setUpModule():
    try:
        import psycopg
    except ImportError:
        raise unittest.SkipTest("no live pgvector -- integration test")
    port = os.environ.get("MIOS_PORTS_PGVECTOR", "8600")
    dsn = f"postgresql://mios:mios@localhost:{port}/mios"
    try:
        with psycopg.connect(dsn, connect_timeout=1):
            pass
    except Exception:
        raise unittest.SkipTest("no live pgvector -- integration test")

class TestMiosBuildCatalog(unittest.TestCase):

    def setUp(self):
        # Folded suites share one interpreter: these parser fakes must not
        # replace the real TOML parser used by VerbCatalogTest afterward.
        self._modules = patch.dict(sys.modules)
        self._modules.start()
        self.addCleanup(self._modules.stop)

    def test_seeding_and_materializing(self):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        toml_data = {
            "packages": {
                "sections": ["base", "repos"],
                "base": {
                    "pkgs": ["firewalld", "audit"]
                },
                "repos": {
                    "pkgs": ["dnf"]
                }
            }
        }
        mock_tomllib = MagicMock()
        mock_tomllib.load.return_value = toml_data
        sys.modules["tomllib"] = mock_tomllib
        sys.modules["tomli"] = mock_tomllib

        import importlib
        seed = importlib.import_module("seed-db-config")

        with patch("psycopg.connect") as mock_connect, \
             patch("builtins.open", mock_open()), \
             patch("os.path.isfile", return_value=True), \
             patch("os.path.isdir", return_value=True), \
             patch("os.listdir", return_value=["01-repos.sh", "02-kernel.sh", "firstboot"]):

            mock_connect.return_value.__enter__.return_value = mock_conn

            res = seed.main()
            self.assertEqual(res, 0)

            calls = [c[0][0] for c in mock_cur.execute.call_args_list]
            self.assertTrue(any("INSERT INTO package_set" in sql for sql in calls))
            self.assertTrue(any("INSERT INTO build_phase" in sql for sql in calls))

    @patch("psycopg.connect")
    @patch("os.makedirs")
    def test_materialization(self, mock_makedirs, mock_connect):
        import importlib
        mat = importlib.import_module("materialize-build-ctx")

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_connect.return_value.__enter__.return_value = mock_conn

        mock_cur.fetchall.side_effect = [
            [{"name": "base", "section": "packages", "pkgs": '["audit"]', "enable": True, "layer": 0, "base_image_ref": None}],
            [{"ordinal": 1, "script": "01-repos.sh", "stage": "container", "deps": '[]'}],
            [{"name": "p1", "policy_type": "type1", "rules": '[]'}],
            [{"name": "prof1", "description": "desc1"}],
            [{"name": "preset1", "description": "desc2", "features": '[]', "debloat_profile_name": "prof1"}]
        ]

        written_files = {}
        def mock_open_impl(path, mode="r", encoding=None):
            m = mock_open()()
            def write_impl(data):
                k = os.path.basename(path)
                written_files[k] = written_files.get(k, "") + data
            m.write.side_effect = write_impl
            return m

        with patch("builtins.open", mock_open_impl), \
             patch.dict(os.environ, {"MIOS_BUILD_CTX": "/tmp/ctx_test"}):

            res = mat.main()
            self.assertEqual(res, 0)

            self.assertIn("package_sets.json", written_files)
            self.assertIn("build_phases.json", written_files)
            self.assertIn("debloat_profiles.json", written_files)

            package_sets = json.loads(written_files["package_sets.json"])
            self.assertEqual(package_sets[0]["name"], "base")
            self.assertEqual(package_sets[0]["pkgs"], ["audit"])


def _run_extra_build_catalog():
    import os
    _saved_env = dict(os.environ)
    try:
        return 0
    except SystemExit as _e:
        return _e.code if _e.code is not None else 0
    finally:
        os.environ.clear()
        os.environ.update(_saved_env)



def _run_all_folded_verbcatalog_suites():
    rc = _run_extra_build_catalog()
    if rc not in (None, 0):
        import sys
        sys.exit(f"Folded test suite failed: exit code {rc}")

if __name__ == "__main__":
    unittest.main()
