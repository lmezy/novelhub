"""YueDu auto-login implementation.

Parses the loginUrl JavaScript from YueDu book sources to extract
login API endpoints and execute HTTP-based authentication.

Supported login patterns:
1. JS-based API login: java.post(url, body, headers) -> JSON response with token
2. JS-based API login: java.ajax(url) -> response with cookies
3. Simple form URL: just a login page URL (auto-login not possible, manual cookie needed)
"""

import asyncio
import json
import logging
import re
from typing import Any
from urllib.parse import urljoin

logger = logging.getLogger(__name__)


class YueduLoginParser:
    """Parse YueDu loginUrl JS and execute the login in Python."""

    def __init__(self, source_config: dict[str, Any], js_runtime=None):
        self.config = source_config
        self.base_url = source_config.get("bookSourceUrl", "")
        self.login_url_js = source_config.get("loginUrl", "")
        self._js_runtime = js_runtime

    def can_auto_login(self) -> bool:
        """Check if the login mechanism is parseable for auto-login."""
        if not self.login_url_js:
            return False
        # JS-based API login
        if "java.post" in self.login_url_js or "java.ajax" in self.login_url_js:
            return True
        if "java.get" in self.login_url_js:
            return True
        # Simple URL (form-based login)
        if self.login_url_js.startswith("http") or "/" in self.login_url_js:
            if len(self.login_url_js) < 200 and "java." not in self.login_url_js:
                return False  # Too short, likely a form URL
            # Long JS code with java.* calls
            if "java." in self.login_url_js:
                return True
        return False

    def get_login_page_url(self) -> str | None:
        """Get the login page URL for form-based login sources."""
        if not self.login_url_js:
            return None
        # If it looks like a simple URL (not JS)
        if len(self.login_url_js) < 500 and "function" not in self.login_url_js and "java." not in self.login_url_js:
            url = self.login_url_js.strip()
            if url.startswith("http"):
                return url
            if url.startswith("/") or "login" in url.lower():
                return urljoin(self.base_url, url)
        return None

    async def execute_login(self, username: str, password: str) -> str | None:
        """Execute the login and return cookie string on success."""
        # If loginUrl is set and contains JS, try API/JS-runtime paths
        if self.login_url_js and self.login_url_js.strip():
            # First try: regex-based parsing (fast path for simple API patterns)
            login_info = self._parse_js_login()
            if login_info:
                cookie = await self._do_api_login(login_info, username, password)
                if cookie:
                    return cookie

            # Second try: use JS runtime to execute the login JS directly
            cookie = await self._execute_js_login(username, password)
            if cookie:
                return cookie

        # Third try: Playwright form-based auto-login
        # Works for both sources with loginUrl (plain URL) and sources
        # without loginUrl (uses bookSourceUrl as default)
        cookie = await self._execute_playwright_login(username, password)
        if cookie:
            return cookie

        return None

    async def _execute_js_login(self, username: str, password: str) -> str | None:
        """Use Node.js JS runtime to execute the loginUrl JS with java.* stubs."""
        if not self._js_runtime:
            # Try to get or create the JsRuntime
            try:
                from app.crawler.plugins.yuedu.js_runtime import JsRuntime
                runtime = JsRuntime.get_instance()
                ok = await runtime.start()
                if not ok:
                    return None
                self._js_runtime = runtime
            except Exception:
                return None

        js_code = self._clean_login_js(self.login_url_js)
        if not js_code:
            return None

        try:
            cookie = await self._js_runtime.eval_login_js(
                js_code, username, password, self.base_url
            )
            return cookie
        except Exception as e:
            logger.warning(f"JS runtime login failed: {e}")
            return None

    async def _execute_playwright_login(
        self, username: str, password: str
    ) -> str | None:
        """Use Playwright to open the login page, fill the form, submit,
        and capture cookies -- replicating Legado's WebView-based login."""
        login_url = self._resolve_login_url()
        if not login_url:
            return None

        # Build a list of candidate login URLs to try
        candidates = [login_url]
        if self.base_url and login_url == self.base_url:
            # Also try common login paths in parallel with the base URL
            from urllib.parse import urljoin
            for path in [
                "/login", "/login.html", "/login.php",
                "/user/login", "/member/login", "/signin",
            ]:
                candidates.append(urljoin(self.base_url, path))

        # Only use Playwright for plain URLs, not JS code
        if self._is_js_code(login_url):
            return None

        try:
            from playwright.async_api import async_playwright
        except ImportError:
            logger.warning("playwright not installed; cannot do form-based login")
            return None

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox"],
            )
            try:
                context = await browser.new_context(
                    viewport={"width": 1280, "height": 720},
                    locale="zh-CN",
                )
                page = await context.new_page()

                # Try each candidate URL until we find a login form
                cookie_str = None
                for url in candidates:
                    if cookie_str:
                        break
                    logger.info(f"Playwright form login: trying {url}")

                    try:
                        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                    except Exception:
                        continue

                    # Auto-fill the login form
                    filled = await self._fill_login_form(page, username, password)
                    if not filled:
                        continue

                    # Submit the form
                    submitted = await self._submit_login_form(page)
                    if not submitted:
                        continue

                    # Wait for login result
                    await self._wait_for_login_result(page)

                    # Capture cookies
                    cookies = await context.cookies()
                    cookie_str = "; ".join(
                        f"{c['name']}={c['value']}" for c in cookies
                        if c.get("name") and c.get("value")
                    )
                    if cookie_str:
                        logger.info(
                            f"Playwright login succeeded on {url} for "
                            f"{self.config.get('bookSourceName', 'unknown')}: "
                            f"{len(cookies)} cookies"
                        )
                        break

                await context.close()
                return cookie_str
            finally:
                await browser.close()

    def _resolve_login_url(self) -> str | None:
        """Resolve the loginUrl to an absolute HTTP URL.

        If loginUrl is empty/missing, defaults to common login paths
        on the source's base URL (matching Legado's WebView behavior).
        """
        from urllib.parse import urljoin

        raw = (self.login_url_js or "").strip()

        # If loginUrl is set, try to resolve it
        if raw:
            cleaned = self._clean_login_js(raw)
            if cleaned.startswith("http://") or cleaned.startswith("https://"):
                return cleaned
            if "{{" in cleaned and "}}" in cleaned:
                cleaned = cleaned.replace("{{baseUrl}}", self.base_url)
                cleaned = cleaned.replace("{{Url()}}", self.base_url)
                cleaned = re.sub(r"\{\{[^}]+\}\}", "", cleaned)
                if cleaned.startswith("http"):
                    return cleaned
            if cleaned.startswith("/"):
                return urljoin(self.base_url, cleaned)
            if self.base_url and not self._is_js_code(cleaned):
                return urljoin(self.base_url, cleaned)

        # No loginUrl or couldn't resolve: try common login paths on base URL
        if self.base_url:
            for path in [
                "/login", "/login.html", "/login.php",
                "/user/login", "/member/login", "/signin",
                "/user", "/member",
            ]:
                candidate = urljoin(self.base_url, path)
                logger.debug(f"Trying login path: {candidate}")
                # We'll try each in _execute_playwright_login
            # Default: use the base URL itself (user may find login link there)
            return self.base_url

        return None

    @staticmethod
    def _is_js_code(text: str) -> bool:
        """Check if text looks like JavaScript code (not a plain URL)."""
        text = text.strip()
        if not text:
            return False
        # Contains JS keywords
        js_keywords = [
            "function", "java.post", "java.get", "java.ajax",
            "JSON.parse", "JSON.stringify", "return ", "var ",
            "let ", "const ", "loginInfo", "@js:", "<js>",
        ]
        for kw in js_keywords:
            if kw in text:
                return True
        # Starts with URL scheme
        if text.startswith("http://") or text.startswith("https://"):
            return False
        # If it has no spaces and looks like a path/URL
        if " " not in text and ("/" in text or "=" in text):
            return False
        return True

    async def _fill_login_form(self, page, username: str, password: str) -> bool:
        """Try to find and fill username/password fields on the login page."""
        # Common username field selectors (ordered by likelihood)
        username_selectors = [
            "input[name='username']",
            "input[name='user']",
            "input[name='account']",
            "input[name='email']",
            "input[name='loginName']",
            "input[name='name']",
            "input[type='text'][name*='user' i]",
            "input[type='text'][name*='account' i]",
            "input[type='text'][name*='login' i]",
            "input[type='email']",
            "input[type='text']:first-of-type",
        ]
        # Common password field selectors
        password_selectors = [
            "input[name='password']",
            "input[name='pass']",
            "input[name='pwd']",
            "input[name='passwd']",
            "input[type='password']",
        ]

        username_el = None
        for sel in username_selectors:
            try:
                username_el = await page.query_selector(sel)
                if username_el:
                    break
            except Exception:
                continue

        password_el = None
        for sel in password_selectors:
            try:
                password_el = await page.query_selector(sel)
                if password_el:
                    break
            except Exception:
                continue

        if not username_el or not password_el:
            return False

        # Clear and fill
        await username_el.click()
        await username_el.fill("")
        await username_el.type(username, delay=50)

        await password_el.click()
        await password_el.fill("")
        await password_el.type(password, delay=50)

        logger.info(f"Filled login form: username field found")
        return True

    async def _submit_login_form(self, page) -> bool:
        """Try to submit the login form by clicking common submit buttons."""
        submit_selectors = [
            "button[type='submit']",
            "input[type='submit']",
            "button:has-text('登录')",
            "button:has-text('Login')",
            "button:has-text('登 录')",
            "button:has-text('登陆')",
            "a:has-text('登录')",
            "a:has-text('Login')",
            "button.btn-primary",
            "button.login-btn",
            "form button",
            "form input[type='submit']",
        ]

        for sel in submit_selectors:
            try:
                btn = await page.query_selector(sel)
                if btn:
                    await btn.click()
                    logger.info(f"Clicked submit button: {sel}")
                    return True
            except Exception:
                continue

        # Fallback: press Enter on the password field
        try:
            pwd = await page.query_selector("input[type='password']")
            if pwd:
                await pwd.press("Enter")
                logger.info("Pressed Enter on password field")
                return True
        except Exception:
            pass

        return False

    async def _wait_for_login_result(self, page) -> None:
        """Wait for login to complete (URL change, cookie set, or element change)."""
        try:
            # WAF pages keep background connections open; DOM readiness is enough.
            await page.wait_for_load_state("domcontentloaded", timeout=8000)
        except Exception:
            pass

        # Brief extra wait for cookie setting
        try:
            await page.wait_for_timeout(3000)
        except Exception:
            pass

        # Try to detect login failure indicators
        try:
            page_text = await page.content()
            if any(
                kw in page_text
                for kw in ["密码错误", "用户名错误", "登录失败", "账号或密码", "login failed"]
            ):
                logger.warning("Login failure detected on page")
        except Exception:
            pass

    @staticmethod
    def _clean_login_js(js: str) -> str:
        """Clean up the login JS: strip @js: prefix, <js> tags, etc."""
        js = js.strip()
        if js.startswith("@js:"):
            js = js[4:].strip()
        if js.startswith("<js>") and js.endswith("</js>"):
            js = js[4:-5].strip()
        if js.startswith("<js") and js.endswith("</js>"):
            # Handle <js> or <js ...> tags
            end_tag = js.find(">")
            if end_tag != -1:
                js = js[end_tag + 1:]
            if js.endswith("</js>"):
                js = js[:-5]
        js = js.strip()
        return js

    # ---- JS Parsing ----

    def _parse_js_login(self) -> dict[str, Any] | None:
        """Parse the loginUrl JS to extract login API details."""
        js = self.login_url_js

        # Extract the login URL
        login_url = self._extract_login_url(js)
        if not login_url:
            return None

        # Determine HTTP method
        method = "POST"
        if "java.get" in js and "java.post" not in js:
            method = "GET"

        # Extract headers
        headers = self._extract_headers(js)

        # Extract body template
        body_template = self._extract_body_template(js)

        # Extract token path from response
        token_path = self._extract_token_path(js)

        # Extract cookie construction pattern
        cookie_pattern = self._extract_cookie_pattern(js)

        return {
            "url": login_url,
            "method": method,
            "headers": headers,
            "body_template": body_template,
            "token_path": token_path,
            "cookie_pattern": cookie_pattern,
        }

    def _extract_login_url(self, js: str) -> str | None:
        """Extract the login API endpoint from JS."""
        # Pattern: java.post(url, ...) or java.get(url, ...)
        # url can be a template literal like `${Url()}/api/user/login`
        patterns = [
            r'java\.(?:post|get|ajax)\s*\(\s*["\']([^"\']+)["\']',
            r'java\.(?:post|get|ajax)\s*\(\s*`([^`]+)`',
            r'let\s+url\s*=\s*[`"]([^`"]+)[`"]',
            r'url\s*=\s*[`"]([^`"]+)[`"]',
        ]
        for pattern in patterns:
            m = re.search(pattern, js)
            if m:
                raw_url = m.group(1)
                # Replace template expressions
                raw_url = raw_url.replace("${Url()}", "")
                raw_url = raw_url.replace("${baseUrl}", "")
                raw_url = re.sub(r'\$\{[^}]+\}', '', raw_url)
                # Remove leading / if base_url ends with /
                if raw_url.startswith("/"):
                    raw_url = raw_url[1:]
                return urljoin(self.base_url, raw_url)

        # Fallback: try to find Url() + "path" pattern
        m = re.search(r'Url\(\)\s*\+\s*["\']([^"\']+)["\']', js)
        if m:
            return urljoin(self.base_url, m.group(1))

        return None

    def _extract_headers(self, js: str) -> dict[str, str]:
        """Extract request headers from JS."""
        headers = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 13) Mobile Safari/537.36",
            "Content-Type": "application/json",
        }

        # Look for header definitions
        header_block = re.search(r'(\{[^}]*"Content-Type"[^}]*\})', js)
        if header_block:
            try:
                hdr_str = header_block.group(1)
                # Clean up JS syntax
                hdr_str = re.sub(r'(\w+):', r'"\1":', hdr_str)
                extracted = json.loads(hdr_str)
                headers.update(extracted)
            except (json.JSONDecodeError, ValueError):
                pass

        # Look for x-requested-with
        if "XMLHttpRequest" in js:
            headers["x-requested-with"] = "XMLHttpRequest"

        return headers

    def _extract_body_template(self, js: str) -> dict[str, Any] | None:
        """Extract the request body template."""
        # Pattern: JSON.stringify({...username...password...})
        m = re.search(r'JSON\.stringify\s*\(\s*(\{[^}]+\})\s*[,\)]', js, re.DOTALL)
        if m:
            body_str = m.group(1)
            # Clean up JS syntax to make it JSON-like
            body_str = re.sub(r'(\w+):', r'"\1":', body_str)
            body_str = re.sub(r',\s*}', '}', body_str)
            try:
                return json.loads(body_str)
            except (json.JSONDecodeError, ValueError):
                pass

        # Pattern: query string like "searchkey=..."
        m = re.search(r'"body"\s*:\s*"([^"]+)"', js)
        if m:
            qs = m.group(1)
            params = {}
            for pair in qs.split("&"):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    params[k] = v
            return params

        # Pattern: form-encoded
        m = re.search(r'"(?:searchkey|username|password)[^"]*=[^"]*"', js)
        if m:
            return {"_form": True}

        return None

    def _extract_token_path(self, js: str) -> str | None:
        """Extract where the token/cookie is in the response."""
        # Pattern: response.data.token
        m = re.search(r'(\w+)\.(\w+)\.(\w+)', js)
        if m:
            return f"{m.group(1)}.{m.group(2)}.{m.group(3)}"

        # Pattern: just token = response.token
        m = re.search(r'token\s*=\s*\w+\.(\w+)', js)
        if m:
            return f"$.{m.group(1)}"

        # Pattern: response.token
        m = re.search(r'response\.(\w+)', js)
        if m:
            return f"$.{m.group(1)}"

        return None

    def _extract_cookie_pattern(self, js: str) -> str | None:
        """Extract how the cookie string is constructed from the token."""
        # Pattern: "authToken=" + token or "Cookie": "xxx=" + token
        patterns = [
            r'"(?:Cookie|cookie)"\s*:\s*"([^"]+=\s*"\s*\+\s*\w+)"',
            r'"Cookie"\s*:\s*"([^"]+)"',
            r'([a-zA-Z]+Token)\s*=\s*(?:token|result)',
            r'"authToken=.*?token',
        ]
        for pattern in patterns:
            m = re.search(pattern, js)
            if m:
                return m.group(0)

        # Default: if there's a token, make cookie as "token=<token>"
        return "token={token}"

    # ---- HTTP Login Execution ----

    async def _do_api_login(self, login_info: dict[str, Any], username: str, password: str) -> str | None:
        """Execute the API login request and return the cookie string."""
        import httpx

        url = login_info["url"]
        method = login_info["method"]
        headers = dict(login_info["headers"])
        body_template = login_info.get("body_template")
        token_path = login_info.get("token_path")
        cookie_pattern = login_info.get("cookie_pattern", "token={token}")

        # Build request body
        body: str | None = None
        if body_template and isinstance(body_template, dict) and not body_template.get("_form"):
            body = json.dumps(body_template)
            body = body.replace('"username"', f'"{username}"') if '"username"' in body else body
            body = body.replace('"password"', f'"{password}"') if '"password"' in body else body
            # Try to match actual field names
            for key in list(body_template.keys()):
                if "user" in key.lower():
                    body = body.replace(f'"{body_template[key]}"', f'"{username}"')
                if "pass" in key.lower() or "pwd" in key.lower():
                    body = body.replace(f'"{body_template[key]}"', f'"{password}"')
        elif body_template and body_template.get("_form"):
            # Form-encoded
            body = f"username={username}&password={password}"

        # If body_template didn't have username/password fields, inject them
        if body is None:
            body = json.dumps({"username": username, "password": password})
            headers["Content-Type"] = "application/json"

        logger.info(f"Yuedu auto-login: {method} {url}")

        try:
            async with httpx.AsyncClient(headers=headers, timeout=30, follow_redirects=False) as client:
                if method == "POST":
                    resp = await client.post(url, content=body)
                else:
                    resp = await client.get(url)

                # Try to extract token/cookie from response
                cookie = self._extract_cookie_from_response(resp, token_path, cookie_pattern)

                if not cookie:
                    # Fallback: collect Set-Cookie headers
                    set_cookies = resp.headers.get_all("set-cookie")
                    if set_cookies:
                        cookie_parts = []
                        for sc in set_cookies:
                            part = sc.split(";")[0].strip()
                            cookie_parts.append(part)
                        cookie = "; ".join(cookie_parts)

                if cookie:
                    logger.info(f"Yuedu auto-login succeeded for {self.config.get('bookSourceName', 'unknown')}")
                    return cookie

                logger.warning(f"Yuedu auto-login: got response but no cookie extracted (status {resp.status_code})")
                return None

        except Exception as e:
            logger.error(f"Yuedu auto-login HTTP failed: {e}")
            return None

    def _extract_cookie_from_response(self, resp, token_path: str | None, cookie_pattern: str) -> str | None:
        """Extract cookie string from HTTP response."""
        # Try parsing JSON response
        try:
            data = resp.json()
            token = None

            # Navigate token path like "response.data.token" or "$.data.token"
            if token_path:
                parts = token_path.replace("$.", "").split(".")
                current = data
                for part in parts:
                    if isinstance(current, dict):
                        current = current.get(part)
                    elif isinstance(current, list) and part.isdigit():
                        current = current[int(part)] if int(part) < len(current) else None
                    else:
                        current = None
                        break
                if current and isinstance(current, str):
                    token = current

            # Try common token field names
            if not token:
                for key in ("token", "access_token", "authToken", "auth_token", "jwt", "session"):
                    if key in data:
                        token = data[key]
                        break
                    if isinstance(data.get("data"), dict) and key in data["data"]:
                        token = data["data"][key]
                        break

            if token:
                return cookie_pattern.replace("{token}", token)

        except (json.JSONDecodeError, ValueError, AttributeError):
            pass

        # Try Set-Cookie headers
        set_cookies = resp.headers.get_all("set-cookie")
        if set_cookies:
            parts = []
            for sc in set_cookies:
                part = sc.split(";")[0].strip()
                if part:
                    parts.append(part)
            if parts:
                return "; ".join(parts)

        return None
