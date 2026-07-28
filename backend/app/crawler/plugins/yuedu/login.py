"""YueDu auto-login implementation.

Parses the loginUrl JavaScript from YueDu book sources to extract
login API endpoints and execute HTTP-based authentication.

Supported login patterns:
1. JS-based API login: java.post(url, body, headers) -> JSON response with token
2. JS-based API login: java.ajax(url) -> response with cookies
3. Simple form URL: just a login page URL (auto-login not possible, manual cookie needed)
"""

import json
import logging
import re
from typing import Any
from urllib.parse import urljoin

logger = logging.getLogger(__name__)


class YueduLoginParser:
    """Parse YueDu loginUrl JS and execute the login in Python."""

    def __init__(self, source_config: dict[str, Any]):
        self.config = source_config
        self.base_url = source_config.get("bookSourceUrl", "")
        self.login_url_js = source_config.get("loginUrl", "")

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
        if not self.login_url_js:
            return None

        # Try parsing JS-based API login
        login_info = self._parse_js_login()
        if login_info:
            return await self._do_api_login(login_info, username, password)

        return None

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
