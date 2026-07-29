"""Manual browser login service -- remote-controlled Playwright sessions.

When auto-login fails, the user can open a headless browser session,
interact with the login page via screenshot+click, and capture cookies.
"""

import asyncio
import base64
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ManualLoginSession:
    """A remote-controlled Playwright browser session for manual login."""

    session_id: str
    source_id: str
    source_name: str
    base_url: str
    login_url: str
    username: str
    password: str
    created_at: float = field(default_factory=time.time)
    _browser: Any = None
    _context: Any = None
    _page: Any = None
    _pw: Any = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def start(self) -> dict:
        """Launch browser, navigate to login page.
        Returns {"screenshot": str, "error": str | None}.
        Uses short timeout so the UI doesn't hang on unreachable sites.
        """
        result = {"screenshot": "", "error": None}

        try:
            from playwright.async_api import async_playwright
        except ImportError:
            logger.error("playwright not installed")
            result["error"] = "Playwright not installed in Docker image"
            return result

        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"],
        )
        # Use hot-swappable proxy config (from admin UI or env vars)
        from app.services.proxy_config import get_playwright_proxy
        proxy_config = get_playwright_proxy()
        if proxy_config:
            logger.info(f"Using proxy: {proxy_config['server']}")
        else:
            logger.info("No proxy configured - direct connection")

        self._context = await self._browser.new_context(
            viewport={"width": 1280, "height": 800},
            locale="zh-CN",
            **(proxy_config and {"proxy": proxy_config} or {}),
        )
        self._page = await self._context.new_page()

        # Navigate with short timeout (10s) so Docker network issues
        # don't hang the UI for 30+ seconds
        nav_ok = False
        nav_error = ""
        for attempt in range(2):
            timeout = 60000 if attempt == 0 else 30000
            try:
                await self._page.goto(
                    self.login_url,
                    wait_until="domcontentloaded",
                    timeout=timeout,
                )
                nav_ok = True
                break
            except Exception as e:
                nav_error = str(e)[:200]
                logger.warning(f"Navigation attempt {attempt + 1} failed: {nav_error}")

        if not nav_ok:
            # Chrome shows its own error page (e.g. ERR_CONNECTION_REFUSED)
            # which is already informative. Just capture it and report the error.
            result["error"] = (
                f"Cannot reach {self.login_url}\n{nav_error}\n\n"
                "The site may be blocking Docker IPs or is unreachable.\n"
                "Try adding a manual Cookie instead."
            )
        else:
            # Pre-fill credentials if form found
            try:
                await self._try_fill_credentials()
            except Exception:
                pass

        result["screenshot"] = await self.screenshot()
        return result

    async def _try_fill_credentials(self) -> None:
        for sel in [
            "input[name='username']", "input[name='user']",
            "input[name='account']", "input[name='email']",
            "input[type='text']:first-of-type",
        ]:
            el = await self._page.query_selector(sel)
            if el:
                await el.fill(self.username)
                break
        for sel in ["input[name='password']", "input[name='pass']", "input[type='password']"]:
            el = await self._page.query_selector(sel)
            if el:
                await el.fill(self.password)
                break

    async def screenshot(self) -> str:
        if not self._page:
            return ""
        data = await self._page.screenshot(type="jpeg", quality=70, full_page=False)
        return "data:image/jpeg;base64," + base64.b64encode(data).decode()

    async def click(self, x: int, y: int) -> str:
        async with self._lock:
            if self._page:
                await self._page.mouse.click(x, y)
                await asyncio.sleep(1.5)
                try:
                    await self._page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    pass
        return await self.screenshot()

    async def type_text(self, text: str) -> str:
        async with self._lock:
            if self._page:
                await self._page.keyboard.type(text, delay=30)
        return await self.screenshot()

    async def press_key(self, key: str) -> str:
        async with self._lock:
            if self._page:
                await self._page.keyboard.press(key)
                await asyncio.sleep(1.0)
                try:
                    await self._page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    pass
        return await self.screenshot()

    async def scroll(self, delta_y: int) -> str:
        async with self._lock:
            if self._page:
                await self._page.evaluate(f"window.scrollBy(0, {delta_y})")
        return await self.screenshot()

    async def get_url(self) -> str:
        if self._page:
            return self._page.url
        return ""

    async def finish(self) -> str | None:
        """Capture cookies and return cookie string."""
        if not self._context:
            return None
        cookies = await self._context.cookies()
        if not cookies:
            return None
        cookie_str = "; ".join(
            f"{c['name']}={c['value']}" for c in cookies
            if c.get("name") and c.get("value")
        )
        await self._cleanup()
        return cookie_str

    async def cancel(self) -> None:
        await self._cleanup()

    async def _cleanup(self) -> None:
        try:
            if self._context:
                await self._context.close()
        except Exception:
            pass
        try:
            if self._browser:
                await self._browser.close()
        except Exception:
            pass
        try:
            if self._pw:
                await self._pw.stop()
        except Exception:
            pass


class ManualLoginManager:
    """In-memory registry of active manual login sessions."""

    _sessions: dict[str, ManualLoginSession] = {}

    @classmethod
    def create_session(
        cls, source_id: str, source_name: str, base_url: str,
        login_url: str, username: str, password: str,
    ) -> ManualLoginSession:
        session_id = uuid.uuid4().hex[:12]
        session = ManualLoginSession(
            session_id=session_id, source_id=source_id,
            source_name=source_name, base_url=base_url,
            login_url=login_url, username=username, password=password,
        )
        cls._sessions[session_id] = session
        return session

    @classmethod
    def get_session(cls, session_id: str) -> ManualLoginSession | None:
        return cls._sessions.get(session_id)

    @classmethod
    def remove_session(cls, session_id: str) -> None:
        cls._sessions.pop(session_id, None)
