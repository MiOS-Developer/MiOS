# AI-hint: Sibling unit test for portal_edge: term_css, the golden gate both ways, portal.py's --card-pad-* wiring and the patched ttyd page and its golden.
# AI-related: /usr/lib/mios/agent-pipe/mios_pipe/routing/portal_edge.py, /usr/lib/mios/agent-pipe/test_mios_portal_edge.py
# AI-functions: TestPortalEdge

import gzip
import hashlib
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from mios_pipe.routing import portal_edge  # noqa: E402

MODULE = os.path.join(HERE, "mios_pipe", "routing", "portal_edge.py")
PORTAL = os.path.join(HERE, "mios_pipe", "routing", "portal.py")
ROOT = os.path.normpath(os.path.join(HERE, "..", "..", "..", ".."))
FIXTURE = os.path.join(ROOT, portal_edge.CSS_GOLDEN)
PAGE = "<head><style>#terminal-container .terminal{height:calc(100% - 10px);padding:5px}</style></head>"

def html_h(page, length_delta=0):
    raw = gzip.compress(page.encode())
    body = ",".join(f"0x{b:02x}" for b in raw)
    return f"unsigned char index_html[] = {{{body}}};\nunsigned int index_html_len = {len(raw) + length_delta};\n".encode()

def ttyd_data(src, pad=0, **over):
    ttyd = {"version": "1.7.7", "page_source": "https://example.invalid/{version}/html.h",
            "page_sha256": hashlib.sha256(src).hexdigest(), "page_path": "/nonexistent/index.html"}
    ttyd.update(over)
    return {"ttyd": ttyd, "theme": {"edge": {"ttyd_padding_px": pad}}}

def edge(**over):
    data = {"theme": {"edge": {"portal_term_chrome_px": 0, "portal_term_bleed": True}}}
    data["theme"]["edge"].update(over)
    return data

class TestPortalEdge(unittest.TestCase):
    def test_vendor_render_matches_fixture(self):
        with open(FIXTURE, encoding="utf-8", newline="") as fh:
            self.assertEqual(portal_edge.term_css(portal_edge.mios_toml.vendor_tree(ROOT)), fh.read())

    def test_chrome_and_bleed(self):
        css = portal_edge.term_css(edge())
        self.assertIn(".card.term.exp .embed-box{border-width:0px;border-radius:0px}", css)
        self.assertIn("margin-left:calc(-1*var(--card-pad-x))", css)
        self.assertIn("margin-bottom:calc(-1*var(--card-pad-bottom))", css)
        self.assertIn("border-width:3px;border-radius:3px", portal_edge.term_css(edge(portal_term_chrome_px=3)))
        self.assertNotIn("calc(", portal_edge.term_css(edge(portal_term_bleed=False)))

    def test_only_terminal_cards(self):
        rules = [ln for ln in portal_edge.term_css(edge()).splitlines() if not ln.startswith("/*")]
        self.assertTrue(rules)
        for rule in rules:
            self.assertTrue(rule.startswith(".card.term.exp "), rule)

    def test_bad_values_name_the_key(self):
        for bad, key in ((edge(portal_term_chrome_px=True), "portal_term_chrome_px"),
                         (edge(portal_term_chrome_px=-1), "portal_term_chrome_px"),
                         (edge(portal_term_chrome_px="0"), "portal_term_chrome_px"),
                         (edge(portal_term_bleed="yes"), "portal_term_bleed"),
                         ({}, "portal_term_chrome_px")):
            with self.assertRaises(ValueError) as ctx:
                portal_edge.term_css(bad)
            self.assertIn(key, str(ctx.exception))

    def test_style_block(self):
        block = portal_edge.style_block(edge())
        self.assertTrue(block.startswith('<style id="mios-edge">') and block.endswith("</style>"), block)
        self.assertEqual(portal_edge.style_block(edge(portal_term_bleed="yes"), safe=True), "")
        with self.assertRaises(ValueError):
            portal_edge.style_block(edge(portal_term_bleed="yes"))

    def test_user_tier_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            user = os.path.join(tmp, "user.toml")
            with open(user, "w", encoding="utf-8") as fh:
                fh.write("[theme.edge]\nportal_term_chrome_px = 2\n")
            env = dict(os.environ, MIOS_USER_TOML=user, MIOS_USER_TOML_D=os.path.join(tmp, "none"),
                       MIOS_HOST_TOML=os.path.join(tmp, "absent.toml"), MIOS_HOST_TOML_D=os.path.join(tmp, "none"),
                       MIOS_VENDOR_TOML=os.path.normpath(os.path.join(HERE, "..", "..", "..", "share", "mios", "mios.toml")))
            out = subprocess.run([sys.executable, MODULE], env=env, capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertIn("border-width:2px", out.stdout)

    def test_no_agent_pipe_deps(self):
        code = f"import sys; sys.path.insert(0, {HERE!r}); from mios_pipe.routing import portal_edge; " \
               "print(sorted(m for m in ('httpx', 'fastapi') if m in sys.modules))"
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual((out.returncode, out.stdout.strip()), (0, "[]"), out.stderr)

    def test_check_fixture_both_sides(self):
        self.assertEqual(portal_edge.main(["--check-fixture", ROOT]), 0)
        with tempfile.TemporaryDirectory() as tmp:
            css, pin = os.path.join(tmp, portal_edge.CSS_GOLDEN), os.path.join(tmp, portal_edge.TTYD_GOLDEN)
            self.assertEqual(portal_edge.main(["--write-fixture", tmp]), 0)
            with open(os.path.join(ROOT, portal_edge.TTYD_GOLDEN), encoding="utf-8") as fh:
                good_pin = fh.read()
            with open(pin, "w", encoding="utf-8") as fh:
                fh.write(good_pin)
            self.assertEqual(portal_edge.main(["--check-fixture", tmp]), 0)
            with open(css, encoding="utf-8", newline="") as fh:
                good = fh.read()
            with open(css, "w", encoding="utf-8", newline="") as fh:
                fh.write(good.replace("border-width:0px", "border-width:1px"))
            with open(pin, "w", encoding="utf-8") as fh:
                fh.write(re.sub(r"padding:\d+px", "padding:5px", good_pin))
            out = subprocess.run([sys.executable, MODULE, "--check-fixture", tmp], capture_output=True, text=True)
            self.assertEqual(out.returncode, 1)
            self.assertIn("portal-term.css", out.stderr)
            self.assertIn("border-width:1px", out.stderr)
            self.assertIn("ttyd-page.json", out.stderr)
            self.assertIn("padding:5px", out.stderr)
            os.chmod(css, 0o640)
            self.assertEqual(portal_edge.main(["--write-fixture", tmp]), 0)
            self.assertEqual(stat.S_IMODE(os.stat(css).st_mode), 0o640)

    def test_portal_lifts_card_padding(self):
        with open(PORTAL, encoding="utf-8") as fh:
            src = fh.read()
        self.assertFalse("padding:15px 15px 13px" in src, "portal.py .card still has the literal padding:15px 15px 13px")
        self.assertTrue("padding:var(--card-pad-top) var(--card-pad-x) var(--card-pad-bottom)" in src, "portal.py .card padding is not --card-pad-*")
        declared = set(re.findall(r"(--card-pad-[a-z]+):\d+px", src))
        used = set(re.findall(r"var\((--card-pad-[a-z]+)\)", portal_edge.term_css(edge())))
        self.assertTrue(used and used <= declared, (used, declared))
        self.assertTrue("portal_edge.style_block(safe=True)" in src, "_portal_theme_css does not append portal_edge.style_block")

    def test_ttyd_page_patch(self):
        src = html_h("<html>" + PAGE + "</html>")
        page = portal_edge.ttyd_page(src, ttyd_data(src))
        self.assertIn("#terminal-container .terminal{height:calc(100% - 0px);padding:0px}", page)
        self.assertNotIn(portal_edge.TTYD_ANCHOR, page)
        self.assertIn("padding:2px", portal_edge.ttyd_page(src, ttyd_data(src, pad=2)))

    def test_mobile_viewport_is_device_width_without_disabling_zoom(self):
        src = html_h("<html>" + PAGE + "</html>")
        page = portal_edge.ttyd_page(src, ttyd_data(src))
        self.assertEqual(page.count('name="viewport"'), 1)
        self.assertIn('content="width=device-width,initial-scale=1"', page)
        self.assertNotIn("user-scalable=no", page)
        for existing in ['<meta name=viewport content="width=980">',
                         '<meta name="viewport" content="width=980">']:
            raw = html_h(PAGE.replace("</head>", existing + "</head>"))
            fixed = portal_edge.ttyd_page(raw, ttyd_data(raw))
            self.assertEqual(fixed.count('name="viewport"'), 1)
            self.assertNotIn("width=980", fixed)

    def test_touch_patch_preserves_focusable_input_and_keyboard_program(self):
        body = ('<body><textarea class="xterm-helper-textarea" tabindex="0"></textarea>'
                '<script>document.querySelector("textarea").focus()</script></body>')
        src = html_h("<html>" + PAGE + body + "</html>")
        page = portal_edge.ttyd_page(src, ttyd_data(src))
        self.assertEqual(re.search(r"<body>.*</body>", page).group(0), body)
        self.assertIn('.xterm-helper-textarea{pointer-events:none}', page)
        self.assertNotIn('disabled', page)
        self.assertNotIn('display:none', page)
        self.assertEqual(page.count('id="mios-terminal-pointer"'), 1)

    def test_mobile_viewport_rejects_missing_head_and_duplicate_tags(self):
        for page, needle in [(PAGE.replace("<head>", "").replace("</head>", ""), "head found 0"),
                             (PAGE.replace("</head>", '<meta name=viewport><meta name="viewport"></head>'), "viewport found 2")]:
            src = html_h(page)
            with self.assertRaisesRegex(ValueError, needle):
                portal_edge.ttyd_page(src, ttyd_data(src))

    def test_launcher_accepts_readable_user_page_and_refuses_missing_explicit_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = os.path.join(tmp, "ttyd")
            with open(fake, "w", encoding="utf-8") as fh:
                fh.write("#!" + sys.executable + "\nimport json,sys\nargs=sys.argv[1:]\n"
                         "print(json.dumps({'page':args[args.index('-I')+1] if '-I' in args else None}))\n")
            os.chmod(fake, 0o700)
            page = os.path.join(tmp, "page.html")
            with open(page, "w", encoding="utf-8") as fh:
                fh.write("<html><head></head></html>")
            launcher = os.path.join(ROOT, "usr/libexec/mios/mios-ttyd-launch")
            env = dict(os.environ, PATH=tmp + os.pathsep + os.environ["PATH"], MIOS_TTYD_PAGE_PATH=page)
            good = subprocess.run(["bash", launcher, "bash"], env=env, capture_output=True, text=True)
            self.assertEqual(good.returncode, 0, good.stderr)
            self.assertIn(page, good.stdout)
            env["MIOS_TTYD_PAGE_PATH"] = os.path.join(tmp, "missing.html")
            bad = subprocess.run(["bash", launcher, "bash"], env=env, capture_output=True, text=True)
            self.assertEqual(bad.returncode, 2)
            self.assertIn("explicit page is not readable", bad.stderr)
            self.assertEqual(bad.stdout, "")

    def test_ttyd_page_fails_loud(self):
        moved = html_h(PAGE.replace("padding:5px", "padding:6px"))
        twice = html_h(PAGE + PAGE)
        short = html_h(PAGE, length_delta=1)
        good = html_h(PAGE)
        for src, data, needle in ((moved, ttyd_data(moved), "found 0 times"),
                                  (twice, ttyd_data(twice), "found 2 times"),
                                  (short, ttyd_data(short), "index_html_len"),
                                  (good, ttyd_data(good, page_sha256="0" * 64), "page_sha256"),
                                  (good, ttyd_data(good, pad=-1), "ttyd_padding_px"),
                                  (good, ttyd_data(good, page_path=""), "page_path")):
            with self.assertRaises(ValueError) as ctx:
                portal_edge.ttyd_page(src, data)
            self.assertIn(needle, str(ctx.exception))

    def test_ttyd_cli_bakes_from_vendor_pin(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, out = os.path.join(tmp, "html.h"), os.path.join(tmp, "ttyd", "index.html")
            raw = html_h(PAGE)
            with open(src, "wb") as fh:
                fh.write(raw)
            vendor = os.path.join(tmp, "vendor.toml")
            with open(vendor, "w", encoding="utf-8") as fh:
                fh.write(f'[ttyd]\nversion = "9.9.9"\npage_source = "https://example.invalid/{{version}}/html.h"\n'
                         f'page_sha256 = "{hashlib.sha256(raw).hexdigest()}"\npage_path = "{out}"\n'
                         "[theme.edge]\nttyd_padding_px = 0\n")
            none = os.path.join(tmp, "none")
            env = dict(os.environ, MIOS_VENDOR_TOML=vendor, MIOS_VENDOR_TOML_D=none, MIOS_HOST_TOML=none,
                       MIOS_HOST_TOML_D=none, MIOS_USER_TOML=none, MIOS_USER_TOML_D=none)
            url = subprocess.run([sys.executable, MODULE, "--ttyd-url"], env=env, capture_output=True, text=True)
            self.assertEqual((url.returncode, url.stdout.strip()), (0, "https://example.invalid/9.9.9/html.h"), url.stderr)
            cli = lambda *a: subprocess.run([sys.executable, MODULE, *a, "--golden-root", tmp], env=env, capture_output=True, text=True)
            self.assertEqual(cli("--write-ttyd-golden", src).returncode, 0)
            run = cli("--ttyd-page", src, "--installed-version", "9.9.9")
            self.assertEqual(run.returncode, 0, run.stderr)
            with open(out, encoding="utf-8") as fh:
                self.assertIn("padding:0px", fh.read())
            os.unlink(out)
            other = cli("--ttyd-page", src, "--installed-version", "9.9.8")
            self.assertEqual(other.returncode, 1)
            self.assertIn("ttyd package 9.9.8 != [ttyd].version 9.9.9", other.stderr)
            pin = os.path.join(tmp, portal_edge.TTYD_GOLDEN)
            with open(pin, encoding="utf-8") as fh:
                good_pin = fh.read()
            with open(pin, "w", encoding="utf-8") as fh:
                fh.write(good_pin.replace("padding:0px", "padding:5px"))
            planted = cli("--ttyd-page", src)
            self.assertEqual(planted.returncode, 1)
            self.assertIn("ttyd-page.json", planted.stderr)
            self.assertIn("padding:5px", planted.stderr)
            self.assertFalse(os.path.exists(out), "a page that differs from its golden was installed")
            with open(src, "wb") as fh:
                fh.write(html_h(PAGE.replace("padding:5px", "padding:6px")))
            bad = cli("--ttyd-page", src)
            self.assertEqual(bad.returncode, 1)
            self.assertIn("page_sha256", bad.stderr)

    def test_ttyd_wiring(self):
        data = portal_edge.mios_toml.vendor_tree(ROOT)
        url, sha, out = portal_edge.ttyd_source(data)
        self.assertIn("/" + data["ttyd"]["version"] + "/", url)
        self.assertEqual(len(sha), 64)
        with open(os.path.join(ROOT, "usr", "libexec", "mios", "mios-ttyd-launch"), encoding="utf-8") as fh:
            launch = fh.read()
        self.assertIn("_toml_get ttyd page_path", launch)
        self.assertIn('ARGS+=("-I" "$TTYD_PAGE")', launch)
        with open(os.path.join(ROOT, "automation", "41-services.sh"), encoding="utf-8") as fh:
            step = fh.read()
        self.assertIn("--ttyd-url", step)
        self.assertIn('--ttyd-page "$_ttyd_src" --installed-version "$(rpm -q --qf \'%{VERSION}\' ttyd)"', step)
        phases = [p["script"] for p in data["build"]["phases"]["list"] if p.get("fatal")]
        self.assertIn("41-services.sh", phases)

if __name__ == "__main__":
    unittest.main()
