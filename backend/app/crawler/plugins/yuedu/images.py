"""Content-image fetching for the YueDu plugin.

Split out of the ``YueduPlugin`` god class.  Manga pages reference
placeholder URLs that never exist, so a 404/410 is remembered (with a
TTL) instead of costing every chapter three retries for the same dead
URL.  A CDN failure must not be recorded against the *source* host's
transport health, which is a mistake this module exists to avoid.
"""

from app.crawler.plugins.yuedu.common import logger
from app.crawler.plugins.yuedu.errors import is_transient_transport_error
import random
import time


class ImagesMixin:
    """Methods extracted from ``YueduPlugin``."""

    @classmethod
    def _image_known_missing(cls, url: str) -> bool:
        """Whether a CDN already answered 404/410 for ``url`` recently."""
        expiry = cls._missing_image_urls.get(url)
        if expiry is None:
            return False
        if expiry < time.monotonic():
            cls._missing_image_urls.pop(url, None)
            return False
        return True
    @classmethod
    def _remember_missing_image(cls, url: str) -> None:
        if len(cls._missing_image_urls) >= cls._missing_image_limit:
            cls._missing_image_urls.clear()
        cls._missing_image_urls[url] = time.monotonic() + cls._missing_image_ttl
    async def fetch_content_image(
        self,
        url: str,
        referer: str | None = None,
    ) -> tuple[bytes, str] | None:
        """Fetch one in-content image, applying imageDecode when configured."""
        import asyncio

        if not url.startswith(("http://", "https://")):
            return None
        clean_url, _ = self._split_options_suffix(url)
        url = clean_url or url
        if self._image_known_missing(url):
            return None
        await self._sleep_rate_limit()
        headers = self._build_headers({
            "Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
        })
        if referer:
            headers["Referer"] = referer

        proxy_url = None
        try:
            from app.services.proxy_config import get_proxy_config
            cfg = get_proxy_config()
            if cfg.enabled:
                proxy_url = cfg.https_proxy or cfg.http_proxy
        except Exception:
            pass

        async def _request(proxy: str | None) -> tuple[bytes, str] | None:
            """Fetch over one transport; ``None`` means "gone for good"."""
            nonlocal headers
            last_error: Exception | None = None
            last_status: int | None = None
            for attempt in range(3):
                try:
                    client = await self._get_http_client(proxy)
                    resp = await client.get(url, headers=headers)
                    if resp.status_code == 403 and attempt == 0:
                        headers = self._with_403_fallback(headers)
                        await asyncio.sleep(1.0 + random.uniform(0.5, 1.0))
                        continue
                    if resp.status_code in (404, 410):
                        # Permanent answer, not a blip: the CDN will not start
                        # serving it during this sync, so do not spend the
                        # retry budget (and the source's rate limit) on it.
                        self._remember_missing_image(url)
                        logger.debug(
                            "Content image missing (HTTP %s): %s",
                            resp.status_code,
                            url,
                        )
                        return None
                    if resp.status_code in (429, 500, 502, 503, 504):
                        retry_after = resp.headers.get("Retry-After", "")
                        wait = (
                            float(retry_after)
                            if retry_after and retry_after.replace(".", "", 1).isdigit()
                            else 2 ** attempt
                        )
                        last_status = resp.status_code
                        await asyncio.sleep(wait + random.uniform(0.5, 1.5))
                        continue
                    resp.raise_for_status()
                    self._capture_cookie_jar(resp)
                    return resp.content, resp.headers.get("content-type", "")
                except Exception as exc:
                    # Includes httpx transport errors *and* the AnyIO stream
                    # errors a half-dead pooled socket raises; both recover on
                    # a fresh connection.
                    if not is_transient_transport_error(exc):
                        raise
                    last_error = exc
                    await self._reset_http_client(proxy)
                    if attempt < 2:
                        await asyncio.sleep((2 ** attempt) + random.uniform(0.5, 1.5))
            if last_error is not None:
                raise last_error
            raise RuntimeError(
                f"Request failed after retries: {url}"
                + (f" (HTTP {last_status})" if last_status else "")
            )

        last_error: Exception | None = None
        for proxy in self._ordered_transports(proxy_url):
            try:
                result = await _request(proxy)
            except Exception as exc:
                if not is_transient_transport_error(exc):
                    last_error = exc
                    break
                last_error = exc
                # An image CDN failing says nothing about the book site, so it
                # must not push the *source's* transport into cooldown; that
                # made the next page request try the dead direct path first.
                if proxy is None:
                    break
                logger.warning(
                    "Configured proxy %s request failed for content image (%s%s); "
                    "retrying direct",
                    proxy_url,
                    type(exc).__name__,
                    f": {exc}" if str(exc) else "",
                )
                continue
            if result is None:
                return None
            data, content_type = result
            if not data or len(data) < 128:
                return None
            if self.engine:
                decoded = self.engine.decode_content_image(data)
                if decoded:
                    data = decoded
            return data, content_type
        if last_error is not None:
            # HTTPX timeouts stringify to "", which produced log lines ending in
            # "…: " with no cause at all.
            logger.warning(
                "Failed to fetch content image %s: %s",
                url,
                str(last_error).strip() or type(last_error).__name__,
            )
        return None
