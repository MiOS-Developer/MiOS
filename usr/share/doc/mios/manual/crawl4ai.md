<!-- AI-hint: Manual pages distilled from the source comments of crawl4ai, sanitized, each passage anchored to the comment it came from. -->

# crawl4ai

### mios-crawl4ai-service -- slim persistent crawl service...

mios-crawl4ai-service -- slim persistent crawl service (loopback FastAPI).

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

<!-- mios-src:2b3f3daa86bf from usr/lib/mios/crawl4ai/mios-crawl4ai-service.py:5-44 -->

### PRIMARY

PRIMARY: drive the EXISTING Chrome over CDP via Playwright DIRECTLY,
    with a hard timeout so a CDP stall can NEVER block the camoufox fallback.

    crawl4ai's AsyncWebCrawler.arun() navigation wrapper fails on the
    ChromeDev flatpak ("Failed on navigating ACS-GOTO" / "[ANTIBOT]"), so we
    use plain Playwright: connect_over_cdp, reuse the browser's default context
    (a --user-data-dir Chrome allows only one), open a page, render, convert
    with crawl4ai's DefaultMarkdownGenerator (the working camoufox pattern).
    The whole attach+nav is wrapped in asyncio.wait_for -- if the sandboxed
    flatpak Chrome stalls the CDP attach (observed), we abort fast and fall
    back to camoufox instead of hanging the request.

<!-- mios-src:7d159965b05f from usr/lib/mios/crawl4ai/mios-crawl4ai-service.py:108-118 -->
### mios-crawl4ai-service -- slim persistent crawl service...

mios-crawl4ai-service -- slim persistent crawl service (loopback FastAPI).

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

<!-- mios-src:0001d7419fb9 from usr/lib/mios/crawl4ai/mios-crawl4ai-service.py:4-43 -->
