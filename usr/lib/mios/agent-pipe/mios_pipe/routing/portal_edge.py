# AI-hint: Portal terminal edge CSS from mios.toml [theme.edge] plus the patched ttyd -I page baked from [ttyd].version; pure, imports only mios_toml
# AI-related: /usr/lib/mios/agent-pipe/mios_pipe/routing/portal.py, /usr/lib/mios/agent-pipe/test_mios_portal_edge.py, /usr/share/mios/theme/fixtures/edge/portal-term.css, /usr/share/mios/theme/fixtures/edge/ttyd-page.json, /automation/41-services.sh
# AI-functions: resolved_data, term_css, style_block, ttyd_source, ttyd_rule, ttyd_page, ttyd_golden, goldens, main

import argparse
import gzip
import hashlib
import json
import logging
import os
import re
import sys

_LIB = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
_TREE = os.path.normpath(os.path.join(_LIB, "..", "..", ".."))
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)
import mios_toml  # noqa: E402 -- the one layered mios.toml loader (Law 13)

STYLE_ID = "mios-edge"
TTYD_ANCHOR = "#terminal-container .terminal{height:calc(100% - 10px);padding:5px}"
HINT = "/* AI-hint: Portal expanded terminal card CSS rendered by portal_edge.py from mios.toml [theme.edge].portal_term_* */"
CSS_GOLDEN = "usr/share/mios/theme/fixtures/edge/portal-term.css"
TTYD_GOLDEN = "usr/share/mios/theme/fixtures/edge/ttyd-page.json"  # the baked page's pin; 41-services.sh fails unless the bake matches it

def resolved_data():
    """The tier-major overlay with drop-ins (Law 13), without the DB overlay or native resolver."""
    return mios_toml.load_merged(mios_toml.layer_paths())

def term_css(data):
    """The expanded terminal card rules; ValueError names a missing or mistyped [theme.edge] key."""
    edge = mios_toml.section(data, "theme.edge")
    chrome, bleed = edge.get("portal_term_chrome_px"), edge.get("portal_term_bleed")
    if isinstance(chrome, bool) or not isinstance(chrome, int) or chrome < 0:
        raise ValueError(f"[theme.edge].portal_term_chrome_px must be a non-negative integer, got {chrome!r}")
    if not isinstance(bleed, bool):
        raise ValueError(f"[theme.edge].portal_term_bleed must be a boolean, got {bleed!r}")
    lines = [HINT, f".card.term.exp .embed-box{{border-width:{chrome}px;border-radius:{chrome}px}}"]
    if bleed:
        lines.append(".card.term.exp .embed{margin-left:calc(-1*var(--card-pad-x));"
                     "margin-right:calc(-1*var(--card-pad-x));margin-bottom:calc(-1*var(--card-pad-bottom))}")
        lines.append(".card.term.exp .embed-bar{padding:0 var(--card-pad-x)}")
    return "\n".join(lines) + "\n"

def style_block(data=None, safe=False):
    """term_css in the <style id="mios-edge"> element portal.py appends; safe=True logs a bad [theme.edge] and returns ""."""
    try:
        return f'<style id="{STYLE_ID}">{term_css(resolved_data() if data is None else data)}</style>'
    except ValueError as exc:
        if not safe:
            raise
        logging.getLogger("mios-agent-pipe").warning("portal: [theme.edge] terminal css skipped: %s", exc)
        return ""

def ttyd_source(data):
    """(url, sha256, out path) for the pinned ttyd page; ValueError names a missing [ttyd] key."""
    ttyd = mios_toml.section(data, "ttyd")
    missing = [k for k in ("version", "page_source", "page_sha256", "page_path") if not ttyd.get(k)]
    if missing:
        raise ValueError(f"[ttyd] lacks {', '.join(missing)}")
    return ttyd["page_source"].format(version=ttyd["version"]), ttyd["page_sha256"], ttyd["page_path"]

def ttyd_rule(data):
    """The .terminal rule the baked page carries, from [theme.edge].ttyd_padding_px."""
    pad = mios_toml.section(data, "theme.edge").get("ttyd_padding_px")
    if isinstance(pad, bool) or not isinstance(pad, int) or pad < 0:
        raise ValueError(f"[theme.edge].ttyd_padding_px must be a non-negative integer, got {pad!r}")
    return f"#terminal-container .terminal{{height:calc(100% - {2 * pad}px);padding:{pad}px}}"

def ttyd_page(html_h, data):
    """ttyd's src/html.h (gzipped index_html[]) decoded with the .terminal rule from ttyd_rule()."""
    rule = ttyd_rule(data)
    _, want, _ = ttyd_source(data)
    got = hashlib.sha256(html_h).hexdigest()
    if got != want:
        raise ValueError(f"ttyd html.h sha256 {got} != [ttyd].page_sha256 {want}")
    text = html_h.decode("ascii")
    body = re.search(r"index_html\[\]\s*=\s*\{([^}]*)\}", text)
    size = re.search(r"index_html_len\s*=\s*(\d+)", text)
    raw = bytes(int(b, 16) for b in re.findall(r"0x([0-9a-fA-F]{2})", body.group(1))) if body else b""
    if not raw or not size or len(raw) != int(size.group(1)):
        raise ValueError("ttyd html.h: index_html[] missing or not index_html_len bytes long")
    page = gzip.decompress(raw).decode("utf-8")
    hits = page.count(TTYD_ANCHOR)
    if hits != 1:
        raise ValueError(f"ttyd page: .terminal padding anchor {TTYD_ANCHOR!r} found {hits} times, expected 1")
    page = page.replace(TTYD_ANCHOR, rule)
    # A mobile browser otherwise uses a desktop-width layout viewport, making
    # every terminal cell microscopic instead of letting the TUI reflow.
    heads = list(re.finditer(r"<head\b[^>]*>(.*?)</head\s*>", page, re.I | re.S))
    if len(heads) != 1:
        raise ValueError(f"ttyd page: head found {len(heads)} times, expected 1")
    head = heads[0]
    meta = '<meta name="viewport" content="width=device-width,initial-scale=1">'
    pattern = r'<meta\b[^>]*\bname\s*=\s*([\'"]?)viewport\1(?=[\s/>])[^>]*>'
    found = list(re.finditer(pattern, head.group(1), re.I))
    if len(found) > 1:
        raise ValueError(f"ttyd page: viewport found {len(found)} times, expected at most 1")
    content = re.sub(pattern, lambda _: meta, head.group(1), count=1, flags=re.I) if found else head.group(1) + meta
    return page[:head.start(1)] + content + page[head.end(1):]

def ttyd_golden(data, page_sha256):
    """The committed pin of the baked page: version, source and page sha256, and the patched .terminal rule."""
    _, source_sha, _ = ttyd_source(data)
    pin = {"version": mios_toml.section(data, "ttyd")["version"], "source_sha256": source_sha,
           "page_sha256": page_sha256, "terminal_rule": ttyd_rule(data)}
    return json.dumps(pin, indent=2) + "\n"

def goldens(root, data):
    """Root-relative golden -> render; the page sha256 is only measurable at bake, so it is read back from the golden."""
    try:
        with open(os.path.join(root, TTYD_GOLDEN), encoding="utf-8") as fh:
            page_sha = json.load(fh).get("page_sha256", "")
    except (OSError, ValueError):
        page_sha = ""
    return {CSS_GOLDEN: term_css(data), TTYD_GOLDEN: ttyd_golden(data, page_sha)}

def main(argv=None):
    parser = argparse.ArgumentParser(description="MiOS Portal edge-to-edge terminal card CSS and ttyd page from [theme.edge]")
    fixture = parser.add_mutually_exclusive_group()
    fixture.add_argument("--check-fixture", metavar="ROOT", help=f"Diff ROOT/{{{CSS_GOLDEN},{TTYD_GOLDEN}}} against the vendor-tier render")
    fixture.add_argument("--write-fixture", metavar="ROOT", help="Regenerate the Portal CSS golden under ROOT from the vendor tier")
    fixture.add_argument("--ttyd-url", action="store_true", help="Print the pinned ttyd html.h URL")
    fixture.add_argument("--ttyd-page", metavar="HTML_H", help="Bake the patched ttyd page from HTML_H and verify it against the golden")
    fixture.add_argument("--write-ttyd-golden", metavar="HTML_H", help="Regenerate the ttyd golden from HTML_H")
    parser.add_argument("--out", metavar="PATH", help="Write --ttyd-page here instead of [ttyd].page_path")
    parser.add_argument("--installed-version", metavar="V", help="With --ttyd-page: the ttyd package version, which must equal [ttyd].version")
    parser.add_argument("--golden-root", metavar="ROOT", default=_TREE, help="The tree holding the ttyd golden (default: this file's)")
    args = parser.parse_args(argv)
    try:
        if not any((args.check_fixture, args.write_fixture, args.ttyd_url, args.ttyd_page, args.write_ttyd_golden)):
            sys.stdout.write(term_css(resolved_data()))
            return 0
        data = mios_toml.vendor_tree(_TREE)
        url, _, out = ttyd_source(data)
        if args.ttyd_url:
            print(url)
            return 0
        if args.check_fixture or args.write_fixture:
            root = args.check_fixture or args.write_fixture
            renders = goldens(root, data)
            return mios_toml.golden_gate("portal_edge", root, {CSS_GOLDEN: renders[CSS_GOLDEN]} if args.write_fixture else renders,
                                         write=bool(args.write_fixture))
        with open(args.ttyd_page or args.write_ttyd_golden, "rb") as fh:
            page = ttyd_page(fh.read(), data)
        pin = ttyd_golden(data, hashlib.sha256(page.encode("utf-8")).hexdigest())
        if args.write_ttyd_golden:
            return mios_toml.golden_gate("portal_edge", args.golden_root, {TTYD_GOLDEN: pin}, write=True)
        version = mios_toml.section(data, "ttyd")["version"]
        if args.installed_version is not None and args.installed_version != version:
            raise ValueError(f"ttyd package {args.installed_version} != [ttyd].version {version}; the page would not match the binary")
        if mios_toml.golden_gate("portal_edge", args.golden_root, {TTYD_GOLDEN: pin}):
            return 1
        mios_toml.write_atomic(args.out or out, page)
        return 0
    except (ValueError, KeyError, OSError) as exc:
        print(f"[portal_edge] ERROR: {exc}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())
