#!/usr/bin/env python3
# AI-hint: FastAPI service providing a persistent crawl4ai/camoufox backend that converts URLs to LLM-ready markdown by maintaining w...
# AI-doc: usr/share/doc/mios/manual/crawl4ai.md
"""mios-crawl4ai-service -- slim persistent crawl service (loopback FastAPI).

The `crawl` broker verb (mios-crawl, the agent-facing surface) POSTs a URL
here and gets back clean, LLM-ready markdown. This service keeps crawl4ai +
camoufox WARM so the (slow) import + browser-attach cost is paid ONCE at
startup, not on every verb call -- same shape as mios-searxng backing the
web_search verb.

ENGINES (operator directive -- container approach SCRAPPED):

  1. PRIMARY: crawl4ai driving the EXISTING local Chrome over CDP.
     MiOS already runs a ChromeDev flatpak with the DevTools Protocol open
     on ws://127.0.0.1:9222 (mios-hermes-browser.service, Hermes's browser
     tool). crawl4ai ATTACHES to that browser instead of launching/bundling
     its own ~2GB Playwright Chromium:
         BrowserConfig(browser_mode="custom", cdp_url=<ws cdp url>)
     -> crawl4ai's browser_manager calls
        playwright.chromium.connect_over_cdp(cdp_url) and reuses the running
        Chrome. NO browser download; `crawl4ai-setup` is NEVER run.

  2. FAIL-RETRY: camoufox (github.com/daijro/camoufox), a stealth/anti-detect
     Firefox. When the CDP crawl errors, is blocked, or returns near-empty
     markdown, the SAME url is retried with AsyncCamoufox (which fetches its
     OWN patched Firefox, ~150MB -- acceptable, it IS the stealth engine).
     camoufox is Firefox, so crawl4ai's Chromium-only connect_over_cdp can't
     drive it natively -> camoufox runs as a SEPARATE path here, and its
     rendered HTML is converted to markdown via crawl4ai's own
     html2text-based generator (no second dependency).

Honest-fail: if BOTH engines fail, the response says so. NEVER fabricate
page content.

SSOT (env rendered from mios.toml [crawl] block via globals/userenv):
  MIOS_SERVICES_WEBTOOLS_CDP_URL    ws://127.0.0.1:9222   Chrome DevTools endpoint to attach
  MIOS_SERVICES_WEBTOOLS_CAMOUFOX   true                   enable the camoufox fail-retry
  MIOS_CRAWL_BIND       127.0.0.1              loopback bind (never LAN)
  MIOS_PORTS_CRAWL4AI    11235                  loopback service port
  MIOS_SERVICES_WEBTOOLS_MIN_CHARS  200                    markdown shorter than this from
                                               CDP triggers the camoufox retry
"""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import BaseModel

from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode

log = logging.getLogger("mios-crawl4ai")

CDP_URL = (os.environ.get("MIOS_SERVICES_WEBTOOLS_CDP_URL")
           or "http://127.0.0.1:%s" % os.environ.get("MIOS_PORTS_CHROME_CDP", "9222")).strip()
CAMOUFOX_ON = os.environ.get("MIOS_SERVICES_WEBTOOLS_CAMOUFOX", "true").strip().lower() in (
    "1", "true", "yes", "on")
MIN_CHARS = int(os.environ.get("MIOS_SERVICES_WEBTOOLS_MIN_CHARS", "200"))
BIND = os.environ.get("MIOS_CRAWL_BIND", "127.0.0.1").strip()
PORT = int(os.environ.get("MIOS_PORTS_CRAWL4AI", "8810"))

_BROWSER_CFG = BrowserConfig(
    browser_mode="custom",   # explicit CDP attach (NOT "dedicated" launch)
    cdp_url=CDP_URL,
    headless=True,           # irrelevant for an attached browser, set for parity
    verbose=False,
)
_crawler: AsyncWebCrawler | None = None
_crawler_lock = asyncio.Lock()

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    global _crawler
    if _crawler is not None:
        try:
            await _crawler.close()
        except Exception:
            pass

app = FastAPI(title="mios-crawl4ai", lifespan=lifespan)

class CrawlReq(BaseModel):
    url: str
    force_camoufox: bool | None = None

def _md_ok(md: str) -> bool:
    return bool(md) and len(md.strip()) >= MIN_CHARS

async def _ensure_crawler() -> AsyncWebCrawler:
    global _crawler
    if _crawler is None:
        async with _crawler_lock:
            if _crawler is None:
                c = AsyncWebCrawler(config=_BROWSER_CFG)
                await c.start()
                _crawler = c
    return _crawler

async def _crawl_cdp(url: str) -> dict:
    """PRIMARY: drive the EXISTING Chrome over CDP via Playwright DIRECTLY,
    with a hard timeout so a CDP stall can NEVER block the camoufox fallback.

    crawl4ai's AsyncWebCrawler.arun() navigation wrapper fails on the
    ChromeDev flatpak ("Failed on navigating ACS-GOTO" / "[ANTIBOT]"), so we
    use plain Playwright: connect_over_cdp, reuse the browser's default context
    (a --user-data-dir Chrome allows only one), open a page, render, convert
    with crawl4ai's DefaultMarkdownGenerator (the working camoufox pattern).
    The whole attach+nav is wrapped in asyncio.wait_for -- if the sandboxed
    flatpak Chrome stalls the CDP attach (observed), we abort fast and fall
    back to camoufox instead of hanging the request."""
    from playwright.async_api import async_playwright
    from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

    async def _do() -> tuple[str, str]:
        _html = ""
        _title = ""
        async with async_playwright() as pw:
            browser = await pw.chromium.connect_over_cdp(CDP_URL)
            ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = await ctx.new_page()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                _html = await page.content()
                try:
                    _title = await page.title()
                except Exception:
                    _title = ""
            finally:
                try:
                    await page.close()   # close the tab only; leave Chrome up
                except Exception:
                    pass
        return _html, _title

    html, title = await asyncio.wait_for(_do(), timeout=float(
        os.environ.get("MIOS_CRAWL_CDP_TIMEOUT", "5")))
    gen = DefaultMarkdownGenerator()
    md_res = gen.generate_markdown(input_html=html, base_url=url)
    markdown = (getattr(md_res, "raw_markdown", None) or "").strip()
    return {
        "ok": bool(markdown),
        "url": url,
        "title": title or "",
        "markdown": markdown,
        "internal_links": 0,
        "external_links": 0,
    }

async def _crawl_camoufox(url: str) -> dict:
    """FAIL-RETRY: stealth Firefox via camoufox -> rendered HTML -> markdown.
    camoufox is imported lazily so the service runs even before
    `python -m camoufox fetch` has pulled its Firefox (CDP-only mode)."""
    from camoufox.async_api import AsyncCamoufox
    from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

    html = ""
    title = ""
    async with AsyncCamoufox(headless=True, geoip=True) as browser:
        page = await browser.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        html = await page.content()
        try:
            title = await page.title()
        except Exception:
            title = ""
    gen = DefaultMarkdownGenerator()
    md_res = gen.generate_markdown(input_html=html, base_url=url)
    markdown = (getattr(md_res, "raw_markdown", None) or "").strip()
    return {
        "ok": bool(markdown),
        "url": url,
        "title": title or "",
        "markdown": markdown,
        "internal_links": 0,
        "external_links": 0,
    }

@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True, "cdp_url": CDP_URL, "camoufox": CAMOUFOX_ON}

@app.post("/crawl")
async def crawl(req: CrawlReq) -> dict:
    url = (req.url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        return {"success": False, "engine": "none", "url": url,
                "title": "", "markdown": "", "links": 0,
                "error": "crawl needs an absolute http(s) URL"}

    force_cam = bool(req.force_camoufox)
    cdp_err = None

    if not force_cam:
        try:
            r = await _crawl_cdp(url)
            if r["ok"] and _md_ok(r["markdown"]):
                return {"success": True, "engine": "chrome-cdp", "url": r["url"],
                        "title": r["title"], "markdown": r["markdown"],
                        "links": r["internal_links"] + r["external_links"]}
            cdp_err = "blocked or near-empty markdown"
        except Exception as e:
            log.warning("CDP crawl failed: %s", e)
            cdp_err = "CDP crawl unavailable"

    if force_cam or CAMOUFOX_ON:
        try:
            r = await _crawl_camoufox(url)
            if r["ok"] and _md_ok(r["markdown"]):
                return {"success": True, "engine": "camoufox", "url": r["url"],
                        "title": r["title"], "markdown": r["markdown"],
                        "links": r["internal_links"] + r["external_links"],
                        "primary_error": cdp_err}
            cam_err = "camoufox returned near-empty markdown"
        except Exception as e:
            log.warning("Camoufox crawl failed: %s", e)
            cam_err = "Camoufox crawl unavailable"
    else:
        cam_err = "camoufox disabled (MIOS_SERVICES_WEBTOOLS_CAMOUFOX=false)"

    return {"success": False, "engine": "none", "url": url, "title": "",
            "markdown": "", "links": 0,
            "error": f"both engines failed (cdp: {cdp_err}; camoufox: {cam_err})"}

def main() -> None:
    import uvicorn
    uvicorn.run(app, host=BIND, port=PORT, log_level="warning")

if __name__ == "__main__":
    main()
