#!/usr/bin/env python3
# AI-hint: Executable proof of ADR-0016's central claim -- that offloading a service to another machine is purely an addressing change, achieved by an...
# AI-doc: usr/share/doc/mios/manual/tests.md
"""Proof: a seat offloads its services by an /etc/mios overlay alone."""

import json
import os
import subprocess
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)

BLADE = "blade-01.mesh.mios.local"

REMOTE = """
[ai]
endpoint = "http://{b}:8700/v1"

[search]
endpoint = "http://{b}:8800/"

[urls]
searxng = "http://{b}:8800"
""".format(b=BLADE)

# "local, localhost or remote" are three values of one mechanism, not three
# mechanisms: the overlay is identical apart from the host it names.
LOCALHOST = REMOTE.replace(BLADE, "localhost")
LAN_IP = REMOTE.replace(BLADE, "10.42.0.7")

OVERLAY = REMOTE

# Resolve in a child so the env is read at process start and no cache is shared.
_PROBE = (
    "import json,os,sys;"
    "sys.path.insert(0, os.path.join(%r,'usr','lib','mios'));"
    "import mios_toml;"
    "d = mios_toml.load_vendor() if os.environ.get('MIOS_PROBE_VENDOR') "
    "else mios_toml.load_merged();"
    "print(json.dumps({'ai': d.get('ai',{}).get('endpoint'),"
    " 'search': d.get('search',{}).get('endpoint'),"
    " 'urls': d.get('urls',{})}))" % _ROOT
)

def _resolve(host_toml=None, vendor_only=False) -> dict:
    env = dict(os.environ)
    env.pop("MIOS_PROBE_VENDOR", None)
    if vendor_only:
        env["MIOS_PROBE_VENDOR"] = "1"
    if host_toml:
        env["MIOS_HOST_TOML"] = host_toml
    else:
        env["MIOS_HOST_TOML"] = os.devnull
    env["MIOS_USER_TOML"] = os.devnull
    out = subprocess.run([sys.executable, "-c", _PROBE], capture_output=True,
                         text=True, env=env, cwd=_ROOT)
    if out.returncode != 0:
        raise AssertionError("resolver failed: %s" % out.stderr.strip()[-400:])
    return json.loads(out.stdout)

def _usr_state() -> str:
    return subprocess.run(["git", "-C", _ROOT, "status", "--porcelain", "usr/"],
                          capture_output=True, text=True, check=False).stdout

class TestOffloadIsAnOverlay(unittest.TestCase):
    """ADR-0016 Decision 1: offload is an addressing change, nothing more."""

    def setUp(self):
        self.before = _usr_state()
        fd, self.overlay = tempfile.mkstemp(suffix=".toml")
        with os.fdopen(fd, "w") as fh:
            fh.write(OVERLAY)

    def tearDown(self):
        os.unlink(self.overlay)

    def test_vendor_tier_is_local(self):
        d = _resolve(vendor_only=True)
        self.assertIn("localhost", d["ai"])
        self.assertIn("localhost", d["search"])
        self.assertIn("localhost", d["urls"]["searxng"])

    def test_overlay_repoints_the_ai_plane(self):
        self.assertIn(BLADE, _resolve(self.overlay)["ai"])

    def test_overlay_repoints_only_what_it_names(self):
        d = _resolve(self.overlay)
        self.assertIn(BLADE, d["search"])
        self.assertIn(BLADE, d["urls"]["searxng"])
        # not in the overlay -> stays local
        self.assertIn("localhost", d["urls"]["forge"])

    def test_local_localhost_and_remote_are_one_mechanism(self):
        """The requirement's three words are three VALUES, not three designs."""
        for name, body, host in (("remote", REMOTE, BLADE),
                                 ("localhost", LOCALHOST, "localhost"),
                                 ("lan", LAN_IP, "10.42.0.7")):
            fd, path = tempfile.mkstemp(suffix=".toml")
            with os.fdopen(fd, "w") as fh:
                fh.write(body)
            try:
                d = _resolve(path)
                self.assertIn(host, d["ai"], name)
                self.assertIn(host, d["search"], name)
            finally:
                os.unlink(path)

    def test_without_the_overlay_everything_is_local(self):
        d = _resolve()
        self.assertIn("localhost", d["ai"])
        self.assertIn("localhost", d["search"])
        self.assertIn("localhost", d["urls"]["searxng"])

    def test_no_file_under_usr_changed(self):
        _resolve(self.overlay)
        self.assertEqual(_usr_state(), self.before,
                         "offload must not require an edit under usr/")

    def test_empty_override_does_not_win(self):
        """Law 1: an empty string never overrides a non-empty value below it."""
        fd, path = tempfile.mkstemp(suffix=".toml")
        with os.fdopen(fd, "w") as fh:
            fh.write('[ai]\nendpoint = ""\n')
        try:
            self.assertIn("localhost", _resolve(path)["ai"])
        finally:
            os.unlink(path)

class TestCanonicalAddressIsTheKeyConsumersRead(unittest.TestCase):
    """The measurement that corrects Decision 1 -- pinned so it cannot rot back."""

    SKIP = ("usr/share/doc/", "usr/share/mios/reference/",
            "usr/share/mios/mios.toml", "usr/share/mios/names.generated.txt",
            "usr/share/mios/referenced_names.txt", "automation/lib/globals.",
            "automation/manifest.json", "tools/manifest.json", "docs/",
            # A fixture is not a consumer: tools/test_render_globals.py carries
            # MIOS_URLS_FORGE as sample data, which is not code reading it.
            "tools/test_",
            "tasks.jsonl", "ROADMAP.md", "ADR.md", "tests/",
            # The emitters are not consumers: the two resolver twins name every
            # alias they emit, and the globals renderer carries fixture names.
            "usr/lib/mios/mios_toml.py", "tools/native/mios-resolver/",
            "tools/native/mios-gen/src/render_globals.rs")

    def _consumers(self, var: str) -> int:
        out = subprocess.run(["git", "-C", _ROOT, "grep", "-lw", var],
                             capture_output=True, text=True, check=False).stdout
        return len([f for f in out.split("\n") if f and not f.startswith(self.SKIP)])

    def test_ai_endpoint_is_read_by_real_consumers(self):
        self.assertGreater(self._consumers("MIOS_AI_ENDPOINT"), 5)

    def _urls_keys(self):
        import tomllib as _t
        with open(os.path.join(_ROOT, "usr/share/mios/mios.toml"), "rb") as fh:
            urls = _t.load(fh)["urls"]
        return sorted(k for k, v in urls.items() if isinstance(v, str))

    def test_every_urls_address_is_read_under_one_name(self):
        # The path-derived naming (MIOS_<TABLE>_<KEY>) made MIOS_URLS_<KEY> the
        # canonical name and left MIOS_<KEY>_URL an accepted INPUT alias. Decision
        # 1's invariant survives the rename: one address, one name its consumers
        # read. Both spellings having readers is the second scheme it forbids.
        for key in self._urls_keys():
            up = key.upper()
            read = [n for n in ("MIOS_URLS_" + up, "MIOS_%s_URL" % up)
                    if self._consumers(n)]
            self.assertLessEqual(len(read), 1, "%s is read as %s" % (key, read))

    def test_migrated_consumers_read_the_canonical_name(self):
        # searxng and forge consumers moved to the canonical spelling; a reader
        # going back to the alias -- or to a hand-composed address -- fails here.
        # If this fails, revisit ADR-0016 Decision 1 rather than deleting it.
        for key in ("SEARXNG", "FORGE"):
            self.assertGreater(self._consumers("MIOS_URLS_" + key), 0, key)
            self.assertEqual(self._consumers("MIOS_%s_URL" % key), 0, key)

    def test_the_four_inter_service_keys_left_urls_and_kept_one_name(self):
        """Decision 1, executed: [urls] is the browser-openable surface only."""
        import tomllib as _t
        with open(os.path.join(_ROOT, "usr/share/mios/mios.toml"), "rb") as fh:
            urls = _t.load(fh)["urls"]
        for gone in ("pgvector", "llm_light", "hermes", "crawl_service"):
            self.assertNotIn(gone, urls, gone)
        for value in urls.values():
            if isinstance(value, str):
                self.assertRegex(value, r"^https?://")

if __name__ == "__main__":
    unittest.main()
