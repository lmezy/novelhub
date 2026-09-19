"""HTTP transport for the YueDu (Legado) plugin.

Split out of the ``YueduPlugin`` god class.  This module owns the whole
request path: the per-(base_url, proxy) client pool and the retirement
dance that keeps a proxy restart from killing in-flight requests that
share a client, transport health ordering, request rate limiting, the
source ``header`` rule, DoH fallback, and the retrying ``_get``/``_post``.

It is a mixin: it only ever touches shared state through ``self`` /
``type(self)``, so the class-level caches stay defined on the assembled
``YueduPlugin`` and every mixin sees the same ones.
"""

from app.crawler.plugins.yuedu.common import logger
from app.crawler.plugins.yuedu.errors import is_transient_transport_error
from typing import Any
from urllib.parse import urlparse
import asyncio
import codecs
import httpx
import json
import os
import random
import re
import time


class TransportMixin:
    """Methods extracted from ``YueduPlugin``."""

    @staticmethod
    def _env_float(name: str, default: float) -> float:
        try:
            value = float(os.getenv(name, "") or default)
        except (TypeError, ValueError):
            return default
        return value if value > 0 else default
    def _http_timeout(self) -> httpx.Timeout:
        """Request timeouts tuned so a hung socket/proxy fails fast.

        A stale pooled connection (the Clash/mihomo proxy restarts, the
        upstream node drops the socket) previously hung for the full 60s read
        timeout, three times per request and three times per chapter -- about
        ten minutes for every chapter of 《要撸小说》 while nothing ever got
        saved.  A shorter read timeout plus a client reset on timeout turns
        that into "one slow request, then success".
        """
        return httpx.Timeout(
            self._env_float("YUEDU_HTTP_READ_TIMEOUT", 25.0),
            connect=self._env_float("YUEDU_HTTP_CONNECT_TIMEOUT", 5.0),
            write=self._env_float("YUEDU_HTTP_WRITE_TIMEOUT", 15.0),
            pool=self._env_float("YUEDU_HTTP_POOL_TIMEOUT", 10.0),
        )
    async def _get_http_client(self, proxy: str | None) -> httpx.AsyncClient:
        """Reuse one AsyncClient per proxy so TLS/connections are pooled."""
        async with self._client_lock:
            client = self.__class__._clients.get(proxy)
            if client is None:
                client = httpx.AsyncClient(
                    timeout=self._http_timeout(),
                    follow_redirects=True,
                    proxy=proxy,
                    trust_env=False,
                    limits=httpx.Limits(
                        max_connections=32,
                        max_keepalive_connections=8,
                        # The proxy restarts often enough that half-open
                        # connections are common; do not keep idle sockets.
                        keepalive_expiry=5.0,
                    ),
                    # Proxy (Clash) TLS interception uses a local CA cert;
                    # browsers accept it interactively but httpx cannot.
                    verify=False,
                )
                self.__class__._clients[proxy] = client
            return client
    async def _reset_http_client(self, proxy: str | None) -> None:
        """Retire the pooled client for ``proxy`` so the retry reconnects.

        httpx happily reuses a pooled socket that the peer already dropped
        (proxies that restarted, upstream nodes that vanished), and only
        reports it after the read timeout.  Forgetting the client forces the
        retry onto a brand new connection.

        The retired client is *not* closed on the spot: one process syncs
        several books and chapters at once over a single shared client, so
        tearing it down under their feet failed every in-flight request at the
        same instant -- on 2026-09-14 a proxy hiccup made 30 unrelated books
        fail within 20 seconds and two tasks abort with a bogus "被反爬"
        message.  The retired client keeps serving the requests that already
        hold it and is closed after ``YUEDU_HTTP_RETIRE_SECONDS`` (default 45s,
        longer than the 25s read timeout).
        """
        async with self._client_lock:
            client = self.__class__._clients.pop(proxy, None)
        if client is not None:
            self.__class__._retire_http_client(client)
    @classmethod
    def _retire_http_client(cls, client: httpx.AsyncClient) -> None:
        """Close a replaced client once requests holding it had time to finish."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop (sync teardown): close what we can immediately.
            try:
                asyncio.run(client.aclose())
            except Exception:
                pass
            return

        task = loop.create_task(
            cls._close_retired_client(client, cls._retire_grace_seconds())
        )
        cls._retired_clients.append((task, loop))
        # Long proxy outages must not leave an unbounded pile of half-retired
        # pools alive; the oldest one has had the longest grace period, so
        # cancelling it still closes it (the close lives in a ``finally``).
        limit = cls._max_retired_clients()
        while len(cls._retired_clients) > limit:
            oldest, oldest_loop = cls._retired_clients.pop(0)
            if not oldest.done():
                try:
                    oldest_loop.call_soon_threadsafe(oldest.cancel)
                except RuntimeError:
                    pass
        task.add_done_callback(cls._forget_retired_client)
    @classmethod
    def _forget_retired_client(cls, task: asyncio.Task) -> None:
        for index, (entry, _loop) in enumerate(cls._retired_clients):
            if entry is task:
                del cls._retired_clients[index]
                return
    @staticmethod
    async def _close_retired_client(client: httpx.AsyncClient, delay: float) -> None:
        try:
            if delay > 0:
                await asyncio.sleep(delay)
        finally:
            try:
                await client.aclose()
            except Exception:
                pass
    @classmethod
    def _retire_grace_seconds(cls) -> float:
        # Read timeout is 25s; wait longer than that before closing.
        return max(0.0, cls._env_float("YUEDU_HTTP_RETIRE_SECONDS", 45.0))
    @classmethod
    def _max_retired_clients(cls) -> int:
        try:
            return max(1, int(os.getenv("YUEDU_HTTP_MAX_RETIRED_CLIENTS", "4") or 4))
        except (TypeError, ValueError):
            return 4
    def _transport_key(self, proxy: str | None) -> str:
        return f"{self.base_url or 'default'}::{'proxy' if proxy else 'direct'}"
    def _transport_in_cooldown(self, proxy: str | None) -> bool:
        until = self.__class__._transport_bad_until.get(
            self._transport_key(proxy),
            0.0,
        )
        return time.monotonic() < until
    def _mark_transport_failure(self, proxy: str | None) -> None:
        cooldown = self._env_float("YUEDU_TRANSPORT_COOLDOWN_SECONDS", 60.0)
        self.__class__._transport_bad_until[self._transport_key(proxy)] = (
            time.monotonic() + cooldown
        )
    def _mark_transport_success(self, proxy: str | None) -> None:
        key = self._transport_key(proxy)
        self.__class__._transport_bad_until.pop(key, None)
        self.__class__._transport_preferred[self.base_url or "default"] = key
    def _ordered_transports(self, proxy_url: str | None) -> list[str | None]:
        """Order the proxy/direct attempts, best candidate first.

        Some sources are only reachable through the proxy (直连 returns
        ConnectTimeout), while the proxy itself restarts now and then, so both
        paths stay available -- ordering merely avoids paying the known-bad
        path on every single request.
        """
        candidates: list[str | None] = []
        if proxy_url:
            candidates.append(proxy_url)
        candidates.append(None)
        preferred = self.__class__._transport_preferred.get(
            self.base_url or "default"
        )

        def _rank(transport: str | None) -> tuple[int, int]:
            return (
                0 if self._transport_in_cooldown(transport) else 1,
                1 if self._transport_key(transport) == preferred else 0,
            )

        return sorted(candidates, key=_rank, reverse=True)
    def _build_headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        """Build request headers from source config, cookies, and per-request extras."""
        headers = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 13) Mobile Safari/537.36",
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,*/*;q=0.8"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
        if self._cookie:
            headers["Cookie"] = self._cookie

        http_user_agent = str(self.config.get("httpUserAgent", "") or "").strip()
        if http_user_agent:
            headers["User-Agent"] = http_user_agent

        header_rule = self.config.get("header", "")
        if header_rule:
            custom_headers = self._custom_headers(header_rule)
            if custom_headers:
                headers.update(custom_headers)

        if extra:
            headers.update(extra)
        if self._cookie or headers.get("Cookie"):
            headers["Cookie"] = self._merge_cookie_strings(
                headers.get("Cookie", ""),
                self._cookie,
            )
        return headers
    def _custom_headers(self, header_rule: Any) -> dict[str, str]:
        """Resolve a Legado ``header`` rule into concrete request headers.

        Sources express this rule as plain JSON *or* as a ``@js:`` script, e.g.
        绅士漫画's::

            @js:
            JSON.stringify({
              "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ...",
              "Referer": baseUrl,
              "Accept-Language": "zh-CN,zh;q=0.9"
            })

        That form used to be fed to ``json.loads`` directly, which always failed
        (``baseUrl`` is not valid JSON and the object spans lines), so the
        declared desktop User-Agent and Referer were silently dropped.  The
        source then received the plugin's mobile UA: 绅士漫画 serves a different
        mobile document that its own ``ruleExplore``/``ruleToc`` cannot match,
        so the sync reported "0 books", and Cloudflare sources saw a UA that no
        longer matched the ``cf_clearance`` the browser had obtained.

        The rule is evaluated through the Node runtime (with ``baseUrl`` in
        scope, like Legado) and the result is cached per source + rule, so the
        per-request cost stays a dict lookup.
        """
        rule = str(header_rule or "").strip()
        if not rule:
            return {}
        cache_key = f"{self.base_url}\u0000{rule}"
        cache = self.__class__._header_rule_cache
        cached = cache.get(cache_key)
        if cached is not None:
            return dict(cached)

        parsed = self._parse_header_rule(rule)
        if len(cache) > 256:
            cache.clear()
        cache[cache_key] = parsed
        return dict(parsed)
    def _parse_header_rule(self, rule: str) -> dict[str, str]:
        """Best-effort parse of one ``header`` rule (never raises)."""
        candidates: list[Any] = []
        is_js = rule.startswith("@js:") or "<js>" in rule
        js_code = rule[4:].strip() if rule.startswith("@js:") else rule

        if not is_js:
            try:
                candidates.append(json.loads(rule))
            except (json.JSONDecodeError, ValueError):
                pass
        else:
            try:
                from app.crawler.plugins.yuedu.js_runtime import JsRuntime

                evaluated = JsRuntime.get_instance().eval_js_sync(
                    js_code,
                    "",
                    context={
                        "baseUrl": self.base_url,
                        "sourceUrl": self.base_url,
                        "bookUrl": self.base_url,
                        "url": self.base_url,
                    },
                )
                if isinstance(evaluated, str):
                    try:
                        evaluated = json.loads(evaluated)
                    except (json.JSONDecodeError, ValueError):
                        pass
                candidates.append(evaluated)
            except Exception as exc:  # pragma: no cover - runtime optional
                logger.debug("header rule JS evaluation failed: %s", exc)

        # Pattern fallback for environments without Node.js: pull the
        # ``JSON.stringify({...})`` literal (or a bare header object) out of the
        # script and quote unquoted keys.  Values that are JS expressions
        # (``baseUrl``) are substituted from the source URL.
        js_object: str | None = None
        match = re.search(r"JSON\.stringify\((\{.+?\})\)", js_code, re.DOTALL)
        if match is not None:
            js_object = match.group(1)
        elif is_js:
            match = re.search(
                r'\{[^{}]*"(?:User-Agent|Content-Type|Cookie|Referer|Accept)[^{}]*\}',
                js_code,
                re.IGNORECASE,
            )
            if match is not None:
                js_object = match.group(0)
        if js_object is not None:
            literal = re.sub(
                r'(?<![A-Za-z0-9_"\':/.-])baseUrl(?![A-Za-z0-9_"-])',
                json.dumps(self.base_url),
                js_object,
            )
            literal = re.sub(r'([{,]\s*)([A-Za-z_][\w\-]*)\s*:', r'\1"\2":', literal)
            literal = re.sub(r",\s*}", "}", literal)
            try:
                candidates.append(json.loads(literal))
            except (json.JSONDecodeError, ValueError):
                pass

        for candidate in candidates:
            if isinstance(candidate, dict):
                cleaned = {
                    str(key): str(value)
                    for key, value in candidate.items()
                    if key and value is not None and not isinstance(value, (dict, list))
                }
                if cleaned:
                    return cleaned
        return {}
    @staticmethod
    def _merge_cookie_strings(*values: str) -> str:
        """Merge Cookie header values without dropping a session cookie."""
        merged: dict[str, str] = {}
        for value in values:
            for part in str(value or "").split(";"):
                part = part.strip()
                if "=" not in part:
                    continue
                name, cookie_value = part.split("=", 1)
                name = name.strip()
                if name:
                    merged[name] = f"{name}={cookie_value.strip()}"
        return "; ".join(merged.values())
    def _with_403_fallback(self, headers: dict[str, str]) -> dict[str, str]:
        """Retry 403 responses with a desktop UA and site Referer."""
        fallback = dict(headers)
        fallback["User-Agent"] = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        )
        fallback.setdefault("Referer", self.base_url.rstrip("/") + "/")
        fallback.setdefault("Accept-Language", "zh-CN,zh;q=0.9,en;q=0.8")
        return fallback
    @staticmethod
    def _looks_polluted(host: str) -> bool:
        """Whether the system-resolved address for host is a loopback/placeholder.

        Chinese sites blocked by the GFW frequently resolve to 127.0.0.1 or
        0.0.0.0 (DNS poisoning). Treating those as unreachable triggers the
        DoH fallback in _get.
        """
        try:
            import socket
            for info in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM):
                ip = info[4][0]
                if ip in ("127.0.0.1", "0.0.0.0", "::1"):
                    return True
                if ip.startswith("127."):
                    return True
            return False
        except Exception:
            return False
    async def _resolve_via_doh(self, host: str) -> str | None:
        """Resolve a host through public DoH providers, with in-process cache.

        Returns the first A record found, or None when every provider fails.
        """
        async with self.__class__._doh_lock:
            cached = self.__class__._doh_cache.get(host)
            now = time.time()
            if cached and float(cached.get("expires", 0)) > now:
                return str(cached.get("ip") or "")

        import httpx as _httpx

        last_error: Exception | None = None
        for provider in self.__class__._doh_providers:
            try:
                params = {"name": host, "type": "A"}
                headers = {"accept": "application/dns-json"}
                async with _httpx.AsyncClient(
                    timeout=_httpx.Timeout(8.0),
                    verify=False,
                    trust_env=False,
                ) as client:
                    resp = await client.get(provider, params=params, headers=headers)
                    if resp.status_code != 200:
                        continue
                    payload = resp.json()
                    answers = payload.get("Answer") or []
                    for answer in answers:
                        data = str(answer.get("data", ""))
                        if data and data not in ("127.0.0.1", "0.0.0.0", "::1"):
                            async with self.__class__._doh_lock:
                                self.__class__._doh_cache[host] = {
                                    "ip": data,
                                    "expires": now + self.__class__._doh_ttl,
                                }
                            logger.info(
                                "DoH %s resolved %s -> %s",
                                provider,
                                host,
                                data,
                            )
                            return data
            except Exception as exc:
                last_error = exc
                continue
        if last_error is not None:
            logger.warning("DoH resolution failed for %s: %s", host, last_error)
        return None
    def _doh_rewrite(self, url: str) -> tuple[str, str] | None:
        """Rewrite a URL to its DoH-resolved IP, keeping the original host.

        Returns (rewritten_url, original_host) or None when no cached IP
        exists (callers resolve via _resolve_via_doh first).
        """
        parts = urlparse(url)
        cached = self.__class__._doh_cache.get(parts.hostname or "")
        if not cached:
            return None
        ip = str(cached.get("ip") or "")
        if not ip:
            return None
        port = ":" + str(parts.port) if parts.port else ""
        rewritten = parts.scheme + "://" + ip + port + (parts.path or "")
        if parts.query:
            rewritten += "?" + parts.query
        if parts.fragment:
            rewritten += "#" + parts.fragment
        return rewritten, parts.hostname or ""
    def _parse_concurrent_rate(self) -> tuple[str, int, int] | None:
        """Parse Legado concurrentRate: "interval" or "count/window"."""
        rate = str(self.config.get("concurrentRate", "") or "").strip()
        if not rate or rate == "0":
            return None
        if "/" in rate:
            try:
                count = max(1, int(rate.split("/", 1)[0].strip()))
                window_ms = max(1, int(rate.split("/", 1)[1].strip()))
            except ValueError:
                return None
            return "window", count, window_ms
        try:
            interval_ms = max(1, int(rate))
        except ValueError:
            return None
        return "interval", 1, interval_ms
    @staticmethod
    def _thread_count() -> int:
        try:
            from app.core.config import sync_thread_count
            return sync_thread_count()
        except Exception:
            return 9
    @staticmethod
    def _rate_limit_disabled() -> bool:
        try:
            from app.core.config import settings
            return bool(getattr(settings, "SYNC_IGNORE_RATE_LIMIT", False))
        except Exception:
            return False
    async def _sleep_rate_limit(self) -> None:
        """Reserve a request slot based on the source concurrentRate.

        A plain integer rate means one request per interval, while
        "count/window" allows count starts per window milliseconds.  Sources
        without concurrentRate fall back to CRAWL_DELAY_MS when it is
        configured, matching Legado's unthrottled behavior by default.

        A per-source interval configured in the admin UI wins over both: the
        site's real 拉取间隔 is a property of the site, not of the rule file,
        and a book source that ships ``concurrentRate: 1000`` for a site that
        only tolerates one request per minute gets the sync captcha-blocked.
        """
        if self._rate_limit_disabled():
            return
        configured = self._request_interval_seconds
        if configured is not None:
            if configured <= 0:
                # Explicitly unthrottled for this source.
                return
            spec = ("interval", 1, configured * 1000)
        else:
            spec = self._parse_concurrent_rate()
            if spec is None:
                try:
                    from app.core.config import settings
                    delay_ms = int(getattr(settings, "CRAWL_DELAY_MS", 0) or 0)
                except Exception:
                    delay_ms = 0
                if delay_ms <= 0:
                    return
                spec = ("interval", 1, delay_ms)
        mode, count, window_ms = spec
        key = self.base_url or "default"
        lock = self.__class__._rate_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self.__class__._rate_locks[key] = lock
        state = self.__class__._rate_state.setdefault(
            key,
            {
                "interval_slot": 0.0,
                "window_start": 0.0,
                "window_used": 0,
                "total_requests": 0,
            },
        )
        async with lock:
            now = time.monotonic()
            if mode == "interval":
                wait_s = state["interval_slot"] + window_ms / 1000.0 - now
                # Add jitter so the request pattern is not a fixed cadence.
                wait_s += random.uniform(0.2, 0.6)
                if wait_s > 0:
                    await asyncio.sleep(wait_s)
                    now = time.monotonic()
                state["interval_slot"] = now
            else:
                window_s = window_ms / 1000.0
                if state["window_start"] + window_s <= now:
                    state["window_start"] = now
                    state["window_used"] = 0
                if state["window_used"] >= count:
                    wait_s = state["window_start"] + window_s - now
                    if wait_s > 0:
                        await asyncio.sleep(wait_s)
                        now = time.monotonic()
                        state["window_start"] = now
                        state["window_used"] = 0
                state["window_used"] += 1

            state["total_requests"] += 1
            total = state["total_requests"]
            try:
                from app.core.config import settings as crawl_settings
                cooldown_every = int(
                    getattr(crawl_settings, "SYNC_RATE_COOLDOWN_EVERY", 0) or 0
                )
                cooldown_seconds = float(
                    getattr(crawl_settings, "SYNC_RATE_COOLDOWN_SECONDS", 0) or 0
                )
            except Exception:
                cooldown_every = 0
                cooldown_seconds = 0
            if (
                cooldown_every > 0
                and cooldown_seconds > 0
                and total % cooldown_every == 0
            ):
                await asyncio.sleep(
                    cooldown_seconds + random.uniform(0, 1)
                )
    def _capture_cookie_jar(self, resp) -> None:
        """Collect Set-Cookie headers when the source enables its cookie jar."""
        if not self.config.get("enabledCookieJar", False):
            return
        set_cookies = resp.headers.get_list("set-cookie")
        if not set_cookies:
            return
        existing = dict(
            (p.split("=", 1)[0], p)
            for p in self._cookie.split("; ")
            if "=" in p
        )
        for sc in set_cookies:
            part = sc.split(";")[0].strip()
            if "=" in part:
                existing[part.split("=", 1)[0]] = part
        self._cookie = "; ".join(existing.values())
    def _request_charset(self, charset: str | None = None) -> str | None:
        """Get the source-declared response encoding, if one exists."""
        value = charset or self.config.get("charset") or self.config.get("pageCharset")
        if not value:
            value = self.config.get("encoding")
        value = str(value or "").strip()
        return value or None
    @staticmethod
    def _response_text(response: Any, charset: str | None = None) -> str:
        """Decode HTML using YueDu's charset option and response metadata.

        ``httpx.Response.text`` assumes UTF-8 for many responses without a
        charset header.  That turns the GBK/Big5 pages used by older Chinese
        sources into replacement characters before the rule engine sees them.
        Decode the raw bytes here while preserving UTF-8 as the normal path.
        """
        raw = getattr(response, "content", None)
        if isinstance(raw, str):
            return raw
        if not isinstance(raw, (bytes, bytearray)):
            return str(getattr(response, "text", "") or "")
        raw = bytes(raw)
        if not raw:
            return ""

        candidates: list[str] = []
        if charset:
            candidates.append(str(charset).strip())

        headers = getattr(response, "headers", {})
        content_type = str(headers.get("content-type", "") or "")
        header_match = re.search(r"charset\s*=\s*[\"']?([^;\"'\s]+)", content_type, re.I)
        if header_match:
            candidates.append(header_match.group(1))

        if raw.startswith(codecs.BOM_UTF8):
            candidates.insert(0, "utf-8-sig")
        elif raw.startswith(codecs.BOM_UTF16_LE) or raw.startswith(codecs.BOM_UTF16_BE):
            candidates.insert(0, "utf-16")
        else:
            meta_match = re.search(
                rb"(?:charset\s*=\s*|content-type[^>]*charset\s*=\s*)[\"']?([a-zA-Z0-9._-]+)",
                raw[:8192],
                re.I,
            )
            if meta_match:
                candidates.append(meta_match.group(1).decode("ascii", errors="ignore"))

        candidates.extend(("utf-8", "gb18030", "big5"))
        seen: set[str] = set()
        for candidate in candidates:
            try:
                codec = codecs.lookup(candidate).name
            except (LookupError, TypeError):
                continue
            if codec in seen:
                continue
            seen.add(codec)
            try:
                return raw.decode(codec)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace")
    async def _post(
        self,
        url: str,
        body: Any = None,
        headers: dict[str, str] | None = None,
        charset: str | None = None,
    ) -> str:
        """HTTP POST with the same retry/proxy behavior as _get."""
        import asyncio
        import httpx

        if not url.startswith(("http://", "https://")):
            raise ValueError(f"Unsupported URL: {url}")
        clean_url, url_options = self._split_options_suffix(url)
        if url_options:
            if url_options.get("headers"):
                merged = dict(headers or {})
                merged.update(url_options.get("headers"))
                headers = merged
            if url_options.get("body") is not None:
                body = url_options.get("body")
            if url_options.get("charset"):
                charset = url_options.get("charset")
            method = str(url_options.get("method", "GET")).upper()
            if method != "POST":
                # A URL option that isn't a POST should go through the matching
                # helper (GET / webView) rather than being force-POSTed.
                return await self._get(clean_url, charset=charset)
            url = clean_url
        await self._sleep_rate_limit()
        headers = self._build_headers(headers)

        proxy_url = None
        try:
            from app.services.proxy_config import get_proxy_config
            cfg = get_proxy_config()
            if cfg.enabled:
                proxy_url = cfg.https_proxy or cfg.http_proxy
        except Exception:
            pass

        async def _request(proxy: str | None) -> str:
            nonlocal headers
            last_error: httpx.HTTPError | None = None
            last_status: int | None = None
            for attempt in range(3):
                try:
                    client = await self._get_http_client(proxy)
                    if isinstance(body, str):
                        content_type = headers.get("Content-Type", "").lower()
                        if "json" in content_type:
                            resp = await client.post(url, content=body, headers=headers)
                        else:
                            resp = await client.post(url, data=body, headers=headers)
                    elif body is None:
                        resp = await client.post(url, headers=headers)
                    else:
                        resp = await client.post(url, json=body, headers=headers)

                    # 403 is the WAF/challenge gate, which a browser can clear.
                    # 5xx (incl. Cloudflare's 520-527) is an upstream failure:
                    # retrying it is useful, rendering it in Chromium is not --
                    # the browser just renders the same error page, which cost a
                    # Chromium launch (and ~50s) per request while 要撸小说 was
                    # answering 520.
                    if resp.status_code == 403:
                        if attempt < 2:
                            headers = self._with_403_fallback(headers)
                            await asyncio.sleep(1.0 + random.uniform(0.5, 1.0))
                            continue
                        try:
                            browser_html = await self._get_with_web_js(
                                url,
                                request_headers=headers,
                                fallback_http=False,
                            )
                        except RuntimeError as exc:
                            if "anti-bot/captcha" in str(exc):
                                raise
                            browser_html = None
                            logger.warning(
                                "Browser fallback failed for %s: %s",
                                url,
                                exc,
                            )
                        except Exception as exc:
                            browser_html = None
                            logger.warning(
                                "Browser fallback failed for %s: %s",
                                url,
                                exc,
                            )
                        if browser_html:
                            return browser_html
                        raise self._blocked_page_error(url)
                    if self._is_transient_upstream_status(resp.status_code):
                        retry_after = resp.headers.get("Retry-After", "")
                        wait = (
                            float(retry_after)
                            if retry_after and retry_after.replace(".", "", 1).isdigit()
                            else 2 ** attempt
                        )
                        # Remember why the last attempt failed: this branch
                        # never sets ``last_error``, so without it the final
                        # error was a bare "Request failed after retries" that
                        # hid a repeated upstream 5xx.
                        last_status = resp.status_code
                        await asyncio.sleep(wait + random.uniform(0.5, 1.5))
                        continue
                    resp.raise_for_status()
                    self._capture_cookie_jar(resp)
                    text = self._response_text(resp, self._request_charset(charset))
                    if self._is_blocked_page(text):
                        if attempt == 0:
                            headers = self._with_403_fallback(headers)
                            await asyncio.sleep(1.0 + random.uniform(0.5, 1.5))
                            continue
                        try:
                            browser_html = await self._get_with_web_js(
                                url,
                                request_headers=headers,
                                fallback_http=False,
                            )
                        except RuntimeError as exc:
                            if "anti-bot/captcha" in str(exc):
                                raise
                            browser_html = None
                            logger.warning(
                                "Browser fallback failed for %s: %s",
                                url,
                                exc,
                            )
                        except Exception as exc:
                            browser_html = None
                            logger.warning(
                                "Browser fallback failed for %s: %s",
                                url,
                                exc,
                            )
                        if browser_html:
                            return browser_html
                        raise self._blocked_page_error(url)
                    return text
                except httpx.HTTPError as exc:
                    last_error = exc
                    if attempt < 2:
                        await asyncio.sleep((2 ** attempt) + random.uniform(0.5, 1.5))
                except Exception as exc:
                    # AnyIO stream errors from a half-dead pooled socket are
                    # not httpx errors, so they need the same retry path.
                    if not is_transient_transport_error(exc):
                        raise
                    await self._reset_http_client(proxy)
                    last_error = exc
                    if attempt < 2:
                        await asyncio.sleep((2 ** attempt) + random.uniform(0.5, 1.5))

            if last_error is not None:
                raise last_error
            raise RuntimeError(
                f"Request failed after retries: {url}"
                + (f" (HTTP {last_status})" if last_status else "")
            )

        last_error: httpx.HTTPError | None = None
        for proxy in self._ordered_transports(proxy_url):
            try:
                html = await _request(proxy)
            except httpx.RequestError as exc:
                last_error = exc
                self._mark_transport_failure(proxy)
                if proxy is None:
                    raise
                logger.warning(
                    "Configured proxy %s request failed (%s%s); retrying direct",
                    proxy_url,
                    type(exc).__name__,
                    f": {exc}" if str(exc) else "",
                )
                continue
            except httpx.HTTPStatusError as exc:
                last_error = exc
                status = (
                    exc.response.status_code
                    if exc.response is not None
                    else None
                )
                # Only retry the other transport for statuses that may be
                # specific to this exit node / IP.  A 404 from the proxy is a
                # definitive answer; falling back to direct just burned the
                # 3x connect timeout before failing anyway.
                if proxy is None or status not in (
                    403, 408, 425, 429, 500, 502, 503, 504, 520, 521, 522, 524,
                ):
                    raise
                logger.warning(
                    "Configured proxy returned HTTP %s; retrying direct",
                    status if status is not None else "error",
                )
                continue
            self._mark_transport_success(proxy)
            return html

        if last_error is not None:
            raise last_error
        raise RuntimeError(f"Request failed after retries: {url}")
    async def _get(self, url: str, charset: str | None = None) -> str:
        """HTTP GET with cookie, headers from config, rate limiting, and cookie jar."""
        import asyncio
        import httpx

        if not url.startswith(("http://", "https://")):
            raise ValueError(f"Unsupported URL: {url}")
        # A source may append ``,{"webView":true}`` / ``,{"method":"POST",...}``
        # to the URL.  Split that off and honor it instead of sending the suffix
        # as part of the path (which breaks /book/123/,{"webView":true} URLs).
        clean_url, url_options = self._split_options_suffix(url)
        if url_options:
            if url_options.get("web_view") or url_options.get("web_js"):
                return await self._get_with_web_js(
                    clean_url,
                    str(url_options.get("web_js") or ""),
                    request_headers=url_options.get("headers") or None,
                    # A ``,{"webView":true}`` suffix means the site *requires* a
                    # browser (e.g. 要撸小说 / forum sources).  Do NOT silently
                    # fall back to plain HTTP when the browser hits an anti-bot
                    # challenge: that only re-requests a page that will never
                    # render over HTTP and turns the real "needs cookie / JS"
                    # cause into a confusing "no usable metadata" error.  Keep
                    # the HTTP fallback for webJs-only rules, which Legado can
                    # still evaluate against the plain-HTTP response.
                    fallback_http=not bool(url_options.get("web_view")),
                )
            if str(url_options.get("method", "GET")).upper() == "POST":
                return await self._post(
                    clean_url,
                    body=url_options.get("body"),
                    headers=url_options.get("headers") or None,
                    charset=url_options.get("charset") or charset,
                )
            url = clean_url
            if url_options.get("charset"):
                charset = url_options.get("charset")
        await self._sleep_rate_limit()
        headers = self._build_headers()

        proxy_url = None
        try:
            from app.services.proxy_config import get_proxy_config
            cfg = get_proxy_config()
            if cfg.enabled:
                proxy_url = cfg.https_proxy or cfg.http_proxy
        except Exception:
            pass

        async def _request(proxy: str | None) -> str:
            nonlocal headers
            last_error: httpx.HTTPError | None = None
            last_status: int | None = None
            # Set when a polluted system DNS forced a DoH-resolved IP rewrite.
            doh_target: tuple[str, str] | None = None
            for attempt in range(3):
                try:
                    client = await self._get_http_client(proxy)
                    req_url = url
                    req_headers = headers
                    if doh_target is not None:
                        req_url, original_host = doh_target
                        req_headers = dict(headers)
                        req_headers["Host"] = original_host
                    resp = await client.get(req_url, headers=req_headers)
                    # 403 is the WAF/challenge gate, which a browser can clear.
                    # 5xx (incl. Cloudflare's 520-527) is an upstream failure:
                    # see :meth:`_is_transient_upstream_status`.
                    if resp.status_code == 403:
                        if attempt < 2:
                            headers = self._with_403_fallback(headers)
                            await asyncio.sleep(1.0 + random.uniform(0.5, 1.0))
                            continue
                        try:
                            browser_html = await self._get_with_web_js(
                                url,
                                request_headers=headers,
                                fallback_http=False,
                            )
                        except RuntimeError as exc:
                            if "anti-bot/captcha" in str(exc):
                                raise
                            browser_html = None
                            logger.warning(
                                "Browser fallback failed for %s: %s",
                                url,
                                exc,
                            )
                        except Exception as exc:
                            browser_html = None
                            logger.warning(
                                "Browser fallback failed for %s: %s",
                                url,
                                exc,
                            )
                        if browser_html:
                            return browser_html
                        # The WAF blocked plain HTTP *and* the browser could not
                        # clear the challenge.  Surface a clear hint instead of a
                        # bare httpx 403 that hides the real cause.
                        raise self._blocked_page_error(url)
                    if self._is_transient_upstream_status(resp.status_code):
                        retry_after = resp.headers.get("Retry-After", "")
                        wait = (
                            float(retry_after)
                            if retry_after and retry_after.replace(".", "", 1).isdigit()
                            else 2 ** attempt
                        )
                        # Remember the status: this branch never sets
                        # ``last_error``, so the final error would otherwise be
                        # a bare "Request failed after retries" that hides a
                        # repeatedly failing upstream 5xx.
                        last_status = resp.status_code
                        await asyncio.sleep(wait + random.uniform(0.5, 1.5))
                        continue
                    resp.raise_for_status()
                    self._capture_cookie_jar(resp)
                    text = self._response_text(resp, self._request_charset(charset))
                    if self._is_blocked_page(text):
                        if attempt == 0:
                            headers = self._with_403_fallback(headers)
                            await asyncio.sleep(1.0 + random.uniform(0.5, 1.5))
                            continue
                        try:
                            browser_html = await self._get_with_web_js(
                                url,
                                request_headers=headers,
                                fallback_http=False,
                            )
                        except RuntimeError as exc:
                            if "anti-bot/captcha" in str(exc):
                                raise
                            browser_html = None
                            logger.warning(
                                "Browser fallback failed for %s: %s",
                                url,
                                exc,
                            )
                        except Exception as exc:
                            browser_html = None
                            logger.warning(
                                "Browser fallback failed for %s: %s",
                                url,
                                exc,
                            )
                        if browser_html:
                            return browser_html
                        raise self._blocked_page_error(url)
                    return text
                except httpx.ConnectError as exc:
                    # DNS pollution bypass: when the direct connect fails and
                    # the system resolver is poisoned (or the site is simply
                    # unreachable by name), resolve via DoH and retry with the
                    # real IP plus an explicit Host header. Only used without
                    # a proxy, since a proxy resolves the name itself.
                    if (
                        proxy is None
                        and doh_target is None
                        and isinstance(exc, httpx.ConnectError)
                    ):
                        hostname = urlparse(url).hostname or ""
                        needs_doh = bool(hostname) and (
                            self._looks_polluted(hostname)
                            or "Name or service not known" in str(exc)
                            or "getaddrinfo failed" in str(exc)
                            or "Temporary failure in name resolution" in str(exc)
                        )
                        if needs_doh:
                            ip = await self._resolve_via_doh(hostname)
                            rewritten = self._doh_rewrite(url) if ip else None
                            if rewritten:
                                doh_target = rewritten
                                logger.warning(
                                    "DNS pollution detected for %s; retrying via %s",
                                    hostname,
                                    rewritten[0],
                                )
                                continue
                    # A dropped/stale socket must not be reused by the retry.
                    await self._reset_http_client(proxy)
                    last_error = exc
                    if attempt < 2:
                        await asyncio.sleep((2 ** attempt) + random.uniform(0.5, 1.5))
                except httpx.HTTPError as exc:
                    if isinstance(exc, httpx.TransportError):
                        # The pooled connection hung (proxy restarted, upstream
                        # node vanished). Drop it so the retry dials fresh
                        # instead of burning another read timeout.
                        await self._reset_http_client(proxy)
                    last_error = exc
                    if attempt < 2:
                        await asyncio.sleep((2 ** attempt) + random.uniform(0.5, 1.5))
                except Exception as exc:
                    # A socket that dies under an in-flight request surfaces as
                    # an AnyIO stream error (``ClosedResourceError``, ``pop
                    # from an empty deque``) rather than an httpx one.  Without
                    # this branch the book/chapter failed outright instead of
                    # dialing again on a fresh connection.
                    if not is_transient_transport_error(exc):
                        raise
                    await self._reset_http_client(proxy)
                    last_error = exc
                    if attempt < 2:
                        await asyncio.sleep((2 ** attempt) + random.uniform(0.5, 1.5))

            if last_error is not None:
                raise last_error
            raise RuntimeError(
                f"Request failed after retries: {url}"
                + (f" (HTTP {last_status})" if last_status else "")
            )

        last_error: httpx.HTTPError | None = None
        for proxy in self._ordered_transports(proxy_url):
            try:
                html = await _request(proxy)
            except httpx.RequestError as exc:
                last_error = exc
                self._mark_transport_failure(proxy)
                if proxy is None:
                    raise
                logger.warning(
                    "Configured proxy %s request failed (%s%s); retrying direct",
                    proxy_url,
                    type(exc).__name__,
                    f": {exc}" if str(exc) else "",
                )
                continue
            except httpx.HTTPStatusError as exc:
                last_error = exc
                status = (
                    exc.response.status_code
                    if exc.response is not None
                    else None
                )
                if proxy is None or status not in (
                    403, 408, 425, 429, 500, 502, 503, 504, 520, 521, 522, 524,
                ):
                    raise
                logger.warning(
                    "Configured proxy returned HTTP %s; retrying direct",
                    status if status is not None else "error",
                )
                continue
            self._mark_transport_success(proxy)
            return html

        if last_error is not None:
            raise last_error
        raise RuntimeError(f"Request failed after retries: {url}")
