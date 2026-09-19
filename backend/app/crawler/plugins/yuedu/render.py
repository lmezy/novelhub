"""Headless-browser rendering for the YueDu (Legado) plugin.

Split out of the ``YueduPlugin`` god class.  A few sources only expose
their content to a real browser (JS-built TOC, ``webJs`` rules, WAF
interstitials), so this module owns Chromium lifecycle, the browser
semaphore that bounds how many pages render at once, cookie
import/capture, and the challenge wait.

The one non-obvious rule encoded here: a caller timeout must never
*cancel* an in-flight ``page.goto``.  Playwright owns that navigation
future; cancelling it left the future unread and asyncio logged
``Future exception was never retrieved`` at GC time.  ``_render_page``
runs the render under ``ensure_future`` + ``asyncio.shield`` and, on
timeout, closes the browser so the navigation can finish on its own.
"""

from app.crawler.plugins.yuedu.common import logger
from typing import Any
import asyncio
import os
import random
import time


class RenderMixin:
    """Methods extracted from ``YueduPlugin``."""

    @classmethod
    def _browser_semaphore(cls) -> asyncio.Semaphore:
        """Per-event-loop semaphore limiting concurrent Chromium instances."""
        key = id(asyncio.get_running_loop())
        semaphore = cls._browser_semaphores.get(key)
        if semaphore is None:
            try:
                limit = int(os.getenv("YUEDU_PLAYWRIGHT_CONCURRENCY", "3") or 3)
            except (TypeError, ValueError):
                limit = 3
            semaphore = asyncio.Semaphore(max(1, limit))
            cls._browser_semaphores[key] = semaphore
        return semaphore
    async def _get_with_web_js(
        self,
        url: str,
        web_js: str = "",
        *,
        request_headers: dict[str, str] | None = None,
        fallback_http: bool = True,
    ) -> str:
        """Fetch a page that requires JavaScript rendering (webJs).

        Uses Playwright to load the page in a headless browser,
        execute the webJs script, and return the resulting HTML.
        
        Falls back to plain HTTP GET if Playwright is unavailable
        or if the webJs execution fails.
        """
        import asyncio

        web_js = str(web_js or "").strip()
        clean_url, url_options = self._split_options_suffix(url)
        if url_options:
            # ``webView:true`` means the site only serves the page to a real
            # browser (e.g. 要撸小说 / forum sources).  A plain-HTTP fallback
            # would just re-request a challenge page, so force it off here
            # regardless of what the caller passed.
            if url_options.get("web_view"):
                fallback_http = False
            if url_options.get("web_js") and not web_js:
                web_js = str(url_options.get("web_js"))
            if url_options.get("headers"):
                merged_headers = dict(request_headers or {})
                merged_headers.update(url_options.get("headers"))
                request_headers = merged_headers
            url = clean_url

        # Try Playwright first
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            logger.warning(
                "playwright not installed; falling back to plain HTTP for webJs"
            )
            if not fallback_http:
                raise RuntimeError(f"Playwright is not installed: {url}")
            return await self._get(url)

        # The source's own rate limit applies to the browser path too; it used
        # to bypass it, which hammered WAF-protected sites with parallel
        # browsers and produced captcha pages mid-sync.
        async with self._browser_semaphore():
            await self._sleep_rate_limit()

        # A single transient browser failure (empty page, navigation timeout)
        # is common right after the proxy reconnects, so retry once when the
        # caller requires a real browser.
        attempts = 1 if fallback_http else 2
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                async with self._browser_semaphore():
                    html = await self._fetch_with_playwright(
                        async_playwright,
                        url,
                        web_js,
                        request_headers,
                    )
                return html
            except Exception as exc:
                last_error = exc
                message = str(exc)
                if "anti-bot/captcha" in message or "is not installed" in message:
                    logger.warning(
                        "Playwright webJs fetch failed for %s: %s%s",
                        url,
                        exc,
                        "; falling back to HTTP" if fallback_http else "",
                    )
                    break
                logger.warning(
                    "Playwright webJs fetch failed for %s: %s (%s/%s)%s",
                    url,
                    exc,
                    attempt + 1,
                    attempts,
                    "; falling back to HTTP" if fallback_http else "",
                )
                if attempt + 1 < attempts:
                    await asyncio.sleep(1.0 + random.uniform(0.5, 1.0))

        # Fallback: try evaluating webJs on plain HTTP response
        if not fallback_http:
            detail = f" ({type(last_error).__name__}: {last_error})" if last_error else ""
            raise RuntimeError(f"Browser request failed: {url}{detail}") from last_error

        html = await self._get(url)
        if self.engine:
            result = self.engine.eval_web_js(web_js, html)
            if result and result != html:
                return result
        return html
    async def _fetch_with_playwright(
        self,
        async_playwright: Any,
        url: str,
        web_js: str,
        request_headers: dict[str, str] | None,
    ) -> str:
        """Render ``url`` in Chromium and return the (webJs-processed) HTML."""
        async with async_playwright() as pw:
            browser = await self._launch_chromium(pw)
            # The render runs as a task that is never cancelled.  A caller that
            # stops waiting -- the cookie health check's per-item timeout, a
            # paused/cancelled crawl task -- used to cancel ``page.goto`` in
            # flight, which orphans Playwright's own navigation future: nothing
            # reads its error, so asyncio reports "Future exception was never
            # retrieved" at ERROR level (three of those per 2am health check).
            # Closing the browser settles the render, and awaiting it here
            # retrieves its error instead of leaking it.
            render = asyncio.ensure_future(
                self._render_page(browser, url, web_js, request_headers)
            )
            try:
                return await asyncio.shield(render)
            except BaseException:
                await self._close_browser(browser)
                try:
                    await render
                except BaseException:
                    pass
                raise
    async def _launch_chromium(self, pw: Any) -> Any:
        """Launch a browser, preferring the full Chromium build.

        Cloudflare/WAF fingerprint checks pass far more often against the full
        browser (new headless) than against the lightweight headless shell; if
        it is not available, fall back to Playwright's default launch.
        """
        launch_kwargs: dict[str, Any] = {
            "headless": True,
            "args": [
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
            ],
        }
        try:
            from app.services.proxy_config import get_playwright_proxy
            proxy = get_playwright_proxy()
            if proxy:
                launch_kwargs["proxy"] = proxy
        except Exception:
            pass
        try:
            return await pw.chromium.launch(
                **launch_kwargs,
                channel="chromium",
            )
        except Exception:
            return await pw.chromium.launch(**launch_kwargs)
    async def _close_browser(self, browser: Any) -> None:
        """Close a browser, tolerating one that is already gone.

        Closing twice happens on the cancellation path (the settling close plus
        the render's own teardown), and the close itself may be interrupted
        while the caller cancels us; neither may mask the error being raised.
        """
        try:
            await browser.close()
        except BaseException:
            pass
    async def _render_page(
        self,
        browser: Any,
        url: str,
        web_js: str,
        request_headers: dict[str, str] | None,
    ) -> str:
        """Render ``url`` in ``browser`` and return the (webJs-processed) HTML."""
        context = None
        try:
            headers = dict(request_headers or self._build_headers())
            user_agent = headers.pop("User-Agent", None)
            # Cookie is installed through the browser cookie jar below.
            cookie_header = self._merge_cookie_strings(
                headers.pop("Cookie", ""),
                self._cookie,
            )
            headers.pop("Connection", None)
            context = await browser.new_context(
                viewport={"width": 1280, "height": 720},
                locale="zh-CN",
                **({"user_agent": user_agent} if user_agent else {}),
                extra_http_headers=headers,
            )
            # Mask common automation fingerprints so challenge pages
            # do not immediately classify the browser as a headless bot.
            try:
                await context.add_init_script(
                    "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
                    "window.chrome=window.chrome||{runtime:{}};"
                    "Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3,4,5]});"
                    "Object.defineProperty(navigator,'languages',{get:()=>['zh-CN','zh','en']});"
                )
            except Exception:
                pass
            page = await context.new_page()

            # Apply cookies if set
            if cookie_header:
                await context.add_cookies(
                    self._parse_cookies_for_playwright(cookie_header)
                )

            # WAF-protected sites often keep analytics sockets open forever;
            # waiting for networkidle turns a usable page into a timeout.
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            # Cloudflare / WAF challenge pages ("Just a moment…") return
            # before the JS challenge has solved itself.  Wait for the
            # real page (and the resolved session cookies) before running
            # any webJs or parsing the content.
            rendered = await self._wait_for_challenge(
                context,
                page,
                url,
                timeout=25.0,
            )
            if not rendered:
                raise RuntimeError(
                    "Site returned an empty browser page (网站返回了空白页): "
                    + url
                )
            if self._is_blocked_page(rendered):
                # Even after waiting the challenge never cleared; surface
                # a clear hint instead of parsing the WAF gate as content.
                raise self._blocked_page_error(url)
            self._capture_playwright_cookies(await context.cookies())

            # Legado webJs may mutate the DOM or return the rendered
            # HTML directly. Preserve both forms instead of discarding
            # the script result.
            if web_js:
                try:
                    html = await page.evaluate(
                        f"(function(){{ var result=document.documentElement.outerHTML; "
                        f"var value=(function(){{ {web_js} }})(); "
                        f"return (typeof value === 'string' && value.trim()) "
                        f"? value : document.documentElement.outerHTML; }})()"
                    )
                except Exception as e:
                    logger.warning(f"webJs execution error: {e}")
                    html = rendered
            else:
                html = rendered

            if self._is_blocked_page(html):
                raise self._blocked_page_error(url)

            return html
        finally:
            if context is not None:
                try:
                    await context.close()
                except Exception:
                    pass
            await self._close_browser(browser)
    def _parse_cookies_for_playwright(
        self,
        cookie_header: str | None = None,
    ) -> list[dict[str, Any]]:
        """Parse cookie string into Playwright cookie format."""
        cookies = []
        cookie_header = str(cookie_header or self._cookie or "")
        if not cookie_header:
            return cookies
        cookie_url = self.base_url.split("##", 1)[0].rstrip("/") + "/"
        for part in cookie_header.split(";"):
            part = part.strip()
            if "=" in part:
                name, value = part.split("=", 1)
                cookies.append({
                    "name": name.strip(),
                    "value": value.strip(),
                    # Let Playwright derive the host, including www/non-www.
                    "url": cookie_url,
                })
        return cookies
    def _capture_playwright_cookies(self, cookies: list[dict[str, Any]]) -> None:
        """Merge cookies set by a browser page into the in-memory cookie string.

        This runs regardless of ``enabledCookieJar`` so that a Cloudflare
        ``cf_clearance`` (and any other session cookie a challenge sets) is
        reused by subsequent plain-HTTP requests.  The merge only mutates the
        in-memory ``self._cookie``; it never writes to the DB cookie store.
        """
        existing = dict(
            (part.split("=", 1)[0].strip(), part.strip())
            for part in self._cookie.split(";")
            if "=" in part
        )
        for cookie in cookies:
            name = str(cookie.get("name") or "").strip()
            if not name:
                continue
            value = str(cookie.get("value") or "")
            existing[name] = f"{name}={value}"
        self._cookie = "; ".join(existing.values())
    async def _wait_for_challenge(
        self,
        context: Any,
        page: Any,
        url: str,
        timeout: float = 25.0,
    ) -> str:
        """Wait for a Cloudflare/WAF JS challenge page to resolve itself.

        Cloudflare serves "Just a moment..." and then runs a JS challenge,
        sets a ``cf_clearance`` cookie, and reloads the page.  A single
        ``page.goto(..., "domcontentloaded")`` returns before that finishes, so
        plain ``page.content()`` captures the challenge gate.  This helper polls
        the live page until either the real content appears (challenge cleared)
        or the deadline passes.  It also performs one explicit ``reload()`` after
        a grace period, which is enough for most challenge flows.
        """
        start = time.monotonic()
        deadline = start + timeout
        last_html = ""
        reloaded = False
        while True:
            try:
                last_html = await page.content()
            except Exception:
                last_html = ""
            # Capture session cookies (cf_clearance etc.) as soon as they appear
            # so later plain-HTTP requests can reuse the cleared session.
            try:
                self._capture_playwright_cookies(await context.cookies())
            except Exception:
                pass
            if last_html and not (
                self._is_challenge_page(last_html)
                or self._is_blocked_page(last_html)
            ):
                return last_html
            if time.monotonic() >= deadline:
                return last_html
            await page.wait_for_timeout(1500)
            # Turnstile / slider challenges auto-solve only after the user widget
            # is ticked.  Best-effort click on the challenge checkbox.
            if "turnstile" in (last_html or "").lower():
                for frame in page.frames:
                    try:
                        checkbox = await frame.query_selector(
                            "input[type=checkbox]"
                        )
                        if checkbox:
                            await checkbox.click(timeout=3000)
                            break
                    except Exception:
                        pass
            # After a grace period issue a single reload.  Cloudflare's challenge
            # runs once then reloads with the clearance cookie; an explicit reload
            # unblocks the rare case where the auto-reload navigation is missed.
            if not reloaded and (time.monotonic() - start) >= 8.0:
                try:
                    await page.reload(
                        wait_until="domcontentloaded",
                        timeout=20000,
                    )
                    reloaded = True
                except Exception:
                    pass
