"""Cookie and login plumbing for the YueDu plugin.

Split out of the ``YueduPlugin`` god class.  ``set_cookie`` records the
*user-configured* Cookie separately from session cookies the site hands
back in ``Set-Cookie``: conflating the two made the plugin tell users to
re-import a Cookie the source never had (a ``fontsize`` preference
cookie was enough).
"""

from app.crawler.plugins.yuedu.common import logger


class AuthMixin:
    """Methods extracted from ``YueduPlugin``."""

    def _sync_cookie_to_engine(self) -> None:
        """Hand the user-imported Cookie to the rule engine's JS context.

        The Cookie is not in the book source JSON (Legado's schema has no field
        for it), so the engine cannot read it from its own config -- it has to be
        pushed.  Without this, JS-issued requests went out unauthenticated and
        ``java.getCookie()`` returned "" for a source the user *had* configured a
        Cookie for (codex-handoff sections 8/19).
        """
        engine = getattr(self, "engine", None)
        if engine is not None:
            engine.set_configured_cookie(getattr(self, "_configured_cookie", ""))

    def set_cookie(self, cookie: str) -> None:
        """Set cookie for authenticated requests."""
        self._cookie = cookie
        self._configured_cookie = cookie
        self._sync_cookie_to_engine()

    async def auto_login(self, username: str, password: str) -> str | None:
        """Attempt auto-login using the source loginUrl mechanism.

        Tries three approaches in order:
        1. Regex-based API login (fast path for simple java.post patterns)
        2. Node.js JS runtime (executes login JS with java.* stubs)
        3. Playwright form login (opens login page, fills form, submits)

        Falls back to None only if all three approaches fail.
        """
        from app.crawler.plugins.yuedu.login import YueduLoginParser
        from app.crawler.plugins.yuedu.js_runtime import JsRuntime

        js_runtime = None
        try:
            js_runtime = JsRuntime.get_instance()
        except Exception:
            pass

        parser = YueduLoginParser(self.config, js_runtime=js_runtime)

        # execute_login now has all three fallbacks built in
        cookie = await parser.execute_login(username, password)
        if cookie:
            return cookie

        logger.warning(f"All login methods failed for {self.display_name}")
        return None
