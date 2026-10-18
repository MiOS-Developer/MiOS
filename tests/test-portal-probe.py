#!/usr/bin/env python3
# AI-hint: Positive and negative controls for usr/libexec/mios/mios-portal-probe, the MiOS-Portal live layout/egress probe: hermetic checks that its socket sampler sees a non-loopback peer (a connected UDP socket to TEST-NET-1 sends nothing), that its resolver-log parser and loopback test classify hosts right, and that its console patterns are the builder smoke's; with MIOS_PORTAL_LIVE=1 it also runs the probe against the installed app (layout + egress must pass) and plants egress (must fail).
# AI-related: usr/libexec/mios/mios-portal-probe, usr/share/mios/portal-build/mios-portal-build-inner, usr/share/mios/mios.toml
"""Hermetic by default; the live cases launch a headless instance of their own (never the operator's)."""

import importlib.machinery
import importlib.util
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("MIOS_ROOT") or os.path.normpath(os.path.join(HERE, ".."))
PROBE = os.path.join(REPO, "usr/libexec/mios/mios-portal-probe")
INNER = os.path.join(REPO, "usr/share/mios/portal-build/mios-portal-build-inner")
LIVE = os.environ.get("MIOS_PORTAL_LIVE") == "1"


def load_probe():
    loader = importlib.machinery.SourceFileLoader("mios_portal_probe", PROBE)
    spec = importlib.util.spec_from_loader("mios_portal_probe", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


P = load_probe()

# Lines from a real smoke log of a build whose policies were refused (20261017T033628Z), and clean ones.
BAD_LINES = [
    "console.error: Policies: \n",
    "  Unable to set preference services.settings.server. Preference not allowed for stability reasons.\n",
    "console.warn: services.settings: Ignoring preference override of remote settings server\n",
]
CLEAN_LINES = [
    "*** You are running in headless mode.\n",
    'console.error: AboutHomeStartupCache: "requestCache called with no _procManager!"\n',
    "Missing chrome or resource URL: resource://gre/modules/UpdateServiceStub.sys.mjs\n",
]


class TestSocketSampler(unittest.TestCase):
    def test_negative_a_non_loopback_peer_is_reported(self):
        # connect() on UDP only records the peer; no packet leaves the host.
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("192.0.2.1", 9))
            peers = P.socket_peers([os.getpid()])
        finally:
            s.close()
        self.assertTrue(any(p.startswith("192.0.2.1:9 ") for p in peers), peers)

    def test_positive_loopback_peers_are_not_egress(self):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        cli = socket.create_connection(srv.getsockname())
        acc, _ = srv.accept()
        try:
            peers = P.socket_peers([os.getpid()])
        finally:
            for x in (cli, acc, srv):
                x.close()
        self.assertEqual([p for p in peers if "127.0.0.1" in p], [])


class TestResolverLog(unittest.TestCase):
    LOG = (
        "2026-10-17 17:01:52.800704 UTC - [Parent 1: Socket Thread]: D/nsHostResolver Resolving host "
        "[localhost]<^partitionKey=%28http%2Clocalhost%29> type 0. [this=7fa0]\n"
        "2026-10-17 17:01:52.915478 UTC - [Parent 1: Socket Thread]: D/nsHostResolver Resolving host [127.0.0.1] type 0.\n"
        "2026-10-17 17:01:53.000000 UTC - [Parent 1: Socket Thread]: D/nsHostResolver Resolving host [aus5.mozilla.org] type 0.\n"
    )

    def test_parse_and_classify(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "moz-resolver.log.moz_log"), "w") as fh:
                fh.write(self.LOG)
            with open(os.path.join(d, "moz-resolver.log.child-1.moz_log"), "w") as fh:
                fh.write("")
            hosts = P.resolved_hosts(d)
        self.assertEqual(hosts, {"localhost", "127.0.0.1", "aus5.mozilla.org"})
        self.assertEqual(sorted(h for h in hosts if not P._host_is_local(h)), ["aus5.mozilla.org"])

    def test_loopback_names(self):
        for h in ("localhost", "127.0.0.1", "::1", "[::1]", "app.localhost"):
            self.assertTrue(P._host_is_local(h), h)
        for h in ("192.0.2.1", "mios-egress-probe.invalid", "localhost.example.com", "content-signature-2.cdn.mozilla.net"):
            self.assertFalse(P._host_is_local(h), h)


class TestConsolePatterns(unittest.TestCase):
    def test_probe_patterns_catch_refused_policies_only(self):
        for ln in BAD_LINES:
            self.assertTrue(any(p.search(ln) for p in P.CONSOLE_FAILURES), ln)
        for ln in CLEAN_LINES:
            self.assertFalse(any(p.search(ln) for p in P.CONSOLE_FAILURES), ln)

    def test_builder_smoke_uses_the_same_patterns(self):
        with open(INNER, encoding="utf-8") as fh:
            m = re.search(r"^SMOKE_CONSOLE_FAILURES='([^']+)'", fh.read(), re.M)
        self.assertTrue(m, "SMOKE_CONSOLE_FAILURES is gone from the builder smoke")
        for ln in BAD_LINES + CLEAN_LINES:
            r = subprocess.run(["grep", "-qE", m.group(1)], input=ln, text=True)
            self.assertEqual(r.returncode == 0, any(p.search(ln) for p in P.CONSOLE_FAILURES), ln)



class TestLayoutSurfaceChecks(unittest.TestCase):
    def _valid_tools(self):
        return [
            {"view": "mios-app-portal", "miosApp": "mios-app-portal", "label": "Portal",
             "x": 8, "y": 680, "w": 32, "h": 32, "shown": True},
            {"view": "mios-app-settings", "miosApp": "mios-app-settings", "label": "MiOS Settings",
             "x": 8, "y": 720, "w": 32, "h": 32, "shown": True},
            {"view": "mios-app-ai-chat", "miosApp": "mios-app-ai-chat", "label": "Portal AI Chat",
             "x": 8, "y": 760, "w": 32, "h": 32, "shown": True},
        ]

    def test_positive_footer_tools_collapsed_and_expanded(self):
        st = {"inner": {"w": 1440, "h": 810}, "tools": self._valid_tools()}
        self.assertEqual(P.check_footer_tools("tabs-collapsed", st, {"right": 49}), [])
        self.assertEqual(P.check_footer_tools("tabs-expanded", st, {"right": 242}), [])

    def test_negative_clipped_footer_button_in_collapsed_rail_fails(self):
        tools = self._valid_tools()
        tools[2]["y"] = 829  # off-screen below 810px window
        st = {"inner": {"w": 1440, "h": 810}, "tools": tools}
        errs = P.check_footer_tools("tabs-collapsed", st, {"right": 49})
        self.assertTrue(any("clipped or off-screen" in e for e in errs), errs)

    def test_negative_firefox_builtin_sidebar_tool_fails(self):
        tools = self._valid_tools()
        tools[0] = {"view": "viewBookmarksSidebar", "miosApp": "", "x": 8, "y": 680, "w": 32, "h": 32, "shown": True}
        st = {"inner": {"w": 1440, "h": 810}, "tools": tools}
        errs = P.check_footer_tools("tabs-collapsed", st, {"right": 49})
        self.assertTrue(any("Firefox built-in tool" in e for e in errs), errs)

    def test_positive_chat_surface_valid(self):
        rv = {
            "inner": {"w": 1440, "h": 810},
            "boxes": {"#sidebar-box": {"x": 1060, "y": 40, "w": 380, "h": 770, "right": 1440}},
            "chat": {
                "loaded": True,
                "href": "chrome://browser/content/genai/chat.html",
                "panel": {"x": 1070, "y": 80, "w": 360, "h": 720, "shown": True},
                "messages": {"x": 1070, "y": 80, "w": 360, "h": 660, "shown": True},
                "input": {"x": 1070, "y": 756, "w": 290, "h": 34, "shown": True},
                "send": {"x": 1366, "y": 756, "w": 64, "h": 34, "shown": True},
                "summarizeShown": False,
                "remoteBrowserShown": False,
                "hasNotFound": False,
            },
        }
        self.assertEqual(P.check_chat_surface(rv), [])

    def test_negative_chat_404_or_summarize_button_fails(self):
        rv = {
            "inner": {"w": 1440, "h": 810},
            "boxes": {"#sidebar-box": {"x": 1060, "y": 40, "w": 380, "h": 770, "right": 1440}},
            "chat": {
                "loaded": True,
                "href": "chrome://browser/content/genai/chat.html",
                "panel": None,
                "messages": None,
                "input": None,
                "send": None,
                "summarizeShown": True,
                "remoteBrowserShown": True,
                "hasNotFound": True,
            },
        }
        errs = P.check_chat_surface(rv)
        self.assertTrue(any("Summarize page" in e for e in errs), errs)
        self.assertTrue(any("Not Found" in e for e in errs), errs)
        self.assertTrue(any("input" in e for e in errs), errs)

    def test_positive_smartwindow_surface_with_mios_ai_valid(self):
        rv = {
            "inner": {"w": 1440, "h": 810},
            "boxes": {"#sidebar-box": {"x": 1060, "y": 0, "w": 380, "h": 810, "right": 1440}},
            "chat": {
                "loaded": True,
                "smartWindow": True,
                "mode": "sidebar",
                "href": "chrome://browser/content/aiwindow/aiWindow.html",
                "panel": {"x": 1074, "y": 80, "w": 352, "h": 710, "shown": True},
                "messages": {"x": 1074, "y": 120, "w": 352, "h": 540, "shown": True},
                "input": {"x": 1080, "y": 690, "w": 330, "h": 34, "shown": True},
                "send": {"x": 1380, "y": 730, "w": 38, "h": 32, "shown": True},
                "modelSelect": {
                    "x": 1160, "y": 730, "w": 140, "h": 28, "shown": True,
                    "label": "MiOS-AI (granite4.1:8b)",
                    "miosAi": True,
                },
                "summarizeShown": False,
                "remoteBrowserShown": False,
                "hasNotFound": False,
            },
        }
        self.assertEqual(P.check_chat_surface(rv), [])

    def test_negative_smartwindow_missing_mios_ai_model_select_fails(self):
        rv = {
            "inner": {"w": 1440, "h": 810},
            "boxes": {"#sidebar-box": {"x": 1060, "y": 0, "w": 380, "h": 810, "right": 1440}},
            "chat": {
                "loaded": True,
                "smartWindow": True,
                "mode": "sidebar",
                "href": "chrome://browser/content/aiwindow/aiWindow.html",
                "panel": {"x": 1074, "y": 80, "w": 352, "h": 710, "shown": True},
                "messages": {"x": 1074, "y": 120, "w": 352, "h": 540, "shown": True},
                "input": {"x": 1080, "y": 690, "w": 330, "h": 34, "shown": True},
                "send": {"x": 1380, "y": 730, "w": 38, "h": 32, "shown": True},
                "modelSelect": {
                    "x": 1160, "y": 730, "w": 140, "h": 28, "shown": True,
                    "label": "Qwen3",
                    "miosAi": False,
                },
                "summarizeShown": False,
                "remoteBrowserShown": False,
                "hasNotFound": False,
            },
        }
        errs = P.check_chat_surface(rv)
        self.assertTrue(any("MiOS-AI model selector" in e for e in errs), errs)


@unittest.skipUnless(LIVE, "set MIOS_PORTAL_LIVE=1 to launch headless MiOS-Portal instances")
class TestLive(unittest.TestCase):
    def probe(self, *argv):
        out = tempfile.mkdtemp(prefix="portal-probe-", dir=os.environ.get("MIOS_PORTAL_PROBE_DIR"))
        r = subprocess.run([sys.executable, "-I", PROBE, *argv, "--out", out], capture_output=True, text=True, timeout=600)
        with open(os.path.join(out, "result.json"), encoding="utf-8") as fh:
            return r, json.load(fh)

    def test_positive_layout(self):
        r, res = self.probe("layout")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(res["ok"])

    def test_positive_no_egress(self):
        r, res = self.probe("egress")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("firefox-bin", res["tracked_processes"].values())

    def test_negative_planted_lookup_fails(self):
        r, res = self.probe("egress", "--seconds", "20", "--plant-url", "http://mios-egress-probe.invalid/")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("mios-egress-probe.invalid", res["non_loopback_hosts"])

    def test_negative_planted_socket_fails(self):
        r, res = self.probe("egress", "--seconds", "20", "--plant-url", "http://192.0.2.1/")
        self.assertNotEqual(r.returncode, 0)
        self.assertTrue(any(p.startswith("192.0.2.1:80 ") for p in res["non_loopback_peers"]), res["non_loopback_peers"])


if __name__ == "__main__":
    unittest.main()
