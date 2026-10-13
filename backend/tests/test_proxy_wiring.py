"""Every browser / curl entry point must honour the admin proxy setting.

The 2026-10-12 audit (``NovelHub代理未生效-修复任务.txt``) found the proxy in
``storage/proxy_config.json`` only reaching manual login, while the yuedu
form/API auto-login, the shared PlaywrightCrawler (起点/番茄), their login
pages and the login-JS curl shim kept dialling out directly -- blocked
sources then failed with ``net::ERR_CONNECTION_CLOSED``.  These tests pin
each of those call sites to the configured proxy.
"""

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import pytest

from app.crawler.playwright_crawler import PlaywrightCrawler
from app.crawler.plugins.fanqie import FanqiePlugin
from app.crawler.plugins.qidian import QidianPlugin
from app.crawler.plugins.yuedu import YueduPlugin
from app.crawler.plugins.yuedu.js_runtime import JsRuntime
from app.crawler.plugins.yuedu.login import YueduLoginParser
from app.services.proxy_config import ProxyConfig, playwright_context_kwargs

PROXY_URL = "http://127.0.0.1:27890"
PROXY = {"server": PROXY_URL}


def _proxy_enabled() -> ProxyConfig:
    return ProxyConfig(enabled=True, https_proxy=PROXY_URL, http_proxy=PROXY_URL)


def test_playwright_context_kwargs_track_the_setting():
    with patch(
        "app.services.proxy_config.get_proxy_config",
        return_value=_proxy_enabled(),
    ):
        assert playwright_context_kwargs() == {"proxy": PROXY}
    with patch(
        "app.services.proxy_config.get_proxy_config",
        return_value=ProxyConfig(enabled=False),
    ):
        assert playwright_context_kwargs() == {}


class _FakePage:
    def __init__(self, context=None):
        self.context = context

    async def goto(self, *args, **kwargs):
        raise RuntimeError("offline in tests")

    async def fill(self, *args, **kwargs):
        return None

    async def click(self, *args, **kwargs):
        return None

    async def wait_for_timeout(self, *args, **kwargs):
        return None


class _FakeContext:
    def __init__(self):
        self.page = _FakePage(self)

    async def new_page(self):
        return self.page

    async def close(self):
        return None

    async def cookies(self):
        return []

    async def add_cookies(self, cookies):
        return None


class _RecordingBrowser:
    """Browser that records the kwargs its context was created with."""

    def __init__(self):
        self.launch_kwargs = None
        self.context_kwargs = None
        self._context = _FakeContext()

    async def new_context(self, **kwargs):
        self.context_kwargs = kwargs
        return self._context

    async def close(self):
        return None


class _RecordingChromium:
    def __init__(self, browser):
        self._browser = browser

    async def launch(self, **kwargs):
        self._browser.launch_kwargs = kwargs
        return self._browser


class _FakePlaywright:
    def __init__(self, browser):
        self.chromium = _RecordingChromium(browser)


class _PlaywrightManager:
    """Stand-in for ``playwright.async_api.async_playwright``.

    Covers both call styles in this codebase: ``async with`` (yuedu login,
    起点/番茄 login) and ``await ...start()`` (PlaywrightCrawler).
    """

    def __init__(self, browser):
        self._browser = browser

    async def start(self):
        return _FakePlaywright(self._browser)

    async def __aenter__(self):
        return _FakePlaywright(self._browser)

    async def __aexit__(self, *exc_info):
        return False


def _fake_playwright(browser):
    """patch.dict target for ``import playwright.async_api`` without the dep."""
    package = ModuleType("playwright")
    package.__path__ = []
    submodule = ModuleType("playwright.async_api")
    submodule.async_playwright = lambda: _PlaywrightManager(browser)
    package.async_api = submodule
    return patch.dict(
        sys.modules,
        {"playwright": package, "playwright.async_api": submodule},
    )


@pytest.mark.asyncio
async def test_web_js_browser_launch_uses_the_configured_proxy():
    plugin = YueduPlugin({"bookSourceUrl": "https://example.com"})

    class _Launcher:
        def __init__(self):
            self.launch_kwargs = None

        async def launch(self, **kwargs):
            self.launch_kwargs = kwargs
            return SimpleNamespace()

    chromium = _Launcher()
    with patch(
        "app.services.proxy_config.get_proxy_config",
        return_value=_proxy_enabled(),
    ):
        await plugin._launch_chromium(SimpleNamespace(chromium=chromium))

    assert chromium.launch_kwargs["proxy"] == PROXY


@pytest.mark.asyncio
async def test_playwright_crawler_context_uses_the_configured_proxy():
    browser = _RecordingBrowser()
    crawler = PlaywrightCrawler()
    with (
        _fake_playwright(browser),
        patch(
            "app.services.proxy_config.get_proxy_config",
            return_value=_proxy_enabled(),
        ),
    ):
        await crawler.start()

    assert browser.context_kwargs["proxy"] == PROXY


@pytest.mark.asyncio
@pytest.mark.parametrize("plugin_cls", [QidianPlugin, FanqiePlugin])
async def test_plugin_auto_login_context_uses_the_configured_proxy(plugin_cls):
    browser = _RecordingBrowser()
    plugin = plugin_cls()
    with (
        _fake_playwright(browser),
        patch(
            "app.services.proxy_config.get_proxy_config",
            return_value=_proxy_enabled(),
        ),
    ):
        # The fake page cannot navigate; what matters is the context kwargs.
        assert await plugin.auto_login("user", "secret") is None

    assert browser.context_kwargs["proxy"] == PROXY


@pytest.mark.asyncio
async def test_yuedu_form_login_context_uses_the_configured_proxy():
    browser = _RecordingBrowser()
    parser = YueduLoginParser({"bookSourceUrl": "https://example.com"})
    with (
        _fake_playwright(browser),
        patch(
            "app.services.proxy_config.get_proxy_config",
            return_value=_proxy_enabled(),
        ),
    ):
        assert await parser._execute_playwright_login("user", "secret") is None

    assert browser.context_kwargs["proxy"] == PROXY


class _FakeResponse:
    status_code = 200

    def __init__(self):
        self.headers = SimpleNamespace(get_all=lambda name: [])

    def json(self):
        return {}


class _RecordingHttpxClient:
    """AsyncClient stand-in that records the kwargs it was built with."""

    last_kwargs: dict | None = None

    def __init__(self, **kwargs):
        type(self).last_kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def get(self, *args, **kwargs):
        return _FakeResponse()

    async def post(self, *args, **kwargs):
        return _FakeResponse()


@pytest.mark.asyncio
async def test_yuedu_api_login_uses_the_configured_proxy():
    parser = YueduLoginParser({"bookSourceUrl": "https://example.com"})
    login_info = {
        "url": "https://example.com/api/login",
        "method": "POST",
        "headers": {},
    }
    with (
        patch("httpx.AsyncClient", _RecordingHttpxClient),
        patch(
            "app.services.proxy_config.get_proxy_config",
            return_value=_proxy_enabled(),
        ),
    ):
        assert await parser._do_api_login(login_info, "user", "secret") is None

    assert _RecordingHttpxClient.last_kwargs["proxy"] == PROXY_URL
    assert _RecordingHttpxClient.last_kwargs["trust_env"] is False


def test_login_js_curl_honours_the_proxy_env():
    script = JsRuntime._build_login_script(
        'java.get("https://example.com/api/login")',
        "user",
        "secret",
        "https://example.com",
    )
    assert "process.env.DSH_HTTP_PROXY" in script
    proxy_line = next(line for line in script.splitlines() if "let proxyArg" in line)
    assert "-x" in proxy_line and "-k" in proxy_line
    curl_line = next(
        line for line in script.splitlines() if "let cmd = " in line and "curl" in line
    )
    assert "proxyArg" in curl_line
