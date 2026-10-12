# AI-hint: Hermetic endpoint-resolution tests for the OWUI entry-point pipe.
# AI-doc: usr/share/doc/mios/manual/tests.md

import importlib.util
import os
import re
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PIPE = os.path.join(_ROOT, "usr/share/mios/owui/pipes/mios_agent_pipe.py")

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover -- py<3.11
    import tomli as tomllib  # type: ignore

def _load_pipe():
    """Import the pipe fresh so Valves() re-reads the current environment."""
    spec = importlib.util.spec_from_file_location("owui_pipe_under_test", _PIPE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def _ssot():
    with open(os.path.join(_ROOT, "usr/share/mios/mios.toml"), "rb") as fh:
        return tomllib.load(fh)

class TestOwuiPipeEndpoints(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        try:
            import pydantic  # noqa: F401
            import aiohttp   # noqa: F401
        except ImportError as exc:
            raise unittest.SkipTest(f"pipe deps absent: {exc!r}")
        cls.cfg = _ssot()

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in
                       ("MIOS_AI_ENDPOINT", "MIOS_PORTS_LLM_LIGHT",
                        "MIOS_REFINE_ENDPOINT", "MIOS_DB_URL")}
        for k in self._saved:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_module_imports(self):
        """The canonical OWUI entry point must load. It did not: `Any` was used
        in a Pipe-body annotation and never imported, and the file sits in
        usr/share/mios, which lint-python did not scan."""
        mod = _load_pipe()
        self.assertTrue(hasattr(mod, "Pipe"), "pipe module exposes no Pipe class")

    def test_backend_url_follows_the_ai_endpoint(self):
        os.environ["MIOS_AI_ENDPOINT"] = "http://example.invalid:9999/v1"
        v = _load_pipe().Pipe.Valves()
        self.assertEqual(v.BACKEND_URL, "http://example.invalid:9999/v1")

    def test_refine_endpoint_follows_the_light_lane(self):
        os.environ["MIOS_PORTS_LLM_LIGHT"] = "9123"
        v = _load_pipe().Pipe.Valves()
        self.assertEqual(v.REFINE_ENDPOINT, "http://127.0.0.1:9123")
        os.environ["MIOS_REFINE_ENDPOINT"] = "http://example.invalid:1/x"
        v = _load_pipe().Pipe.Valves()
        self.assertEqual(v.REFINE_ENDPOINT, "http://example.invalid:1/x",
                         "an explicit MIOS_REFINE_ENDPOINT must win")

    def test_defaults_match_the_port_ssot(self):
        ports = self.cfg["ports"]
        v = _load_pipe().Pipe.Valves()
        self.assertIn(str(ports["agent_pipe"]), v.BACKEND_URL,
                      "BACKEND_URL default must name [ports].agent_pipe")
        self.assertIn(str(ports["llm_light"]), v.REFINE_ENDPOINT,
                      "REFINE_ENDPOINT default must name [ports].llm_light")

    def test_no_retired_port_in_either_default(self):
        retired = {str(p) for p in self.cfg["docs"]["retired_ports"]}
        v = _load_pipe().Pipe.Valves()
        for name, url in (("BACKEND_URL", v.BACKEND_URL),
                          ("REFINE_ENDPOINT", v.REFINE_ENDPOINT)):
            found = set(re.findall(r":(\d{2,5})", url)) & retired
            self.assertFalse(found, f"{name} names retired port(s) {found}: {url}")

    def test_decommissioned_writes_are_off_by_default(self):
        mod = _load_pipe()
        self.assertEqual(mod._DB_URL, "",
                         "the retired-datastore writes must not be on by default")
        os.environ["MIOS_DB_URL"] = "http://example.invalid:8000"
        mod = _load_pipe()
        self.assertEqual(mod._DB_URL, "http://example.invalid:8000",
                         "setting MIOS_DB_URL must still re-enable them")

def main():
    return 0 if unittest.main(exit=False, verbosity=2).result.wasSuccessful() else 1

if __name__ == "__main__":
    sys.exit(main())
