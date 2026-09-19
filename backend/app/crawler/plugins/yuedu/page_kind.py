"""Deciding what a fetched page actually is.

Split out of the ``YueduPlugin`` god class.  These predicates answer
"is this a captcha gate / an upstream 5xx stub / a deleted book / a
blocked chapter?" using the windowed vocabulary in ``markers``.

They exist because a bare substring search over a whole page is not
evidence: Cloudflare injects its bot-management script into *healthy*
pages, a novel title can contain 限流, and a chapter's own prose can say
已被限制 or 身份验证.  Every predicate here is one of those hard-won
distinctions, so change them only with a real page in hand.
"""

from app.crawler.plugins.yuedu.markers import CF_CHALLENGE_MARKERS
from app.crawler.plugins.yuedu.markers import CLOUDFLARE_ERROR_IDS
from app.crawler.plugins.yuedu.markers import CLOUDFLARE_ERROR_PHRASES
from app.crawler.plugins.yuedu.markers import REMOVED_PAGE_MARKERS
from app.crawler.plugins.yuedu.markers import STRONG_BLOCK_MARKERS
from app.crawler.plugins.yuedu.markers import UPSTREAM_ERROR_BODY_RE
from app.crawler.plugins.yuedu.markers import UPSTREAM_ERROR_CODE_RE
from app.crawler.plugins.yuedu.markers import UPSTREAM_ERROR_PAGE_MAX_CHARS
from app.crawler.plugins.yuedu.markers import has_contextual_block_marker
from app.crawler.plugins.yuedu.markers import has_weak_block_marker
from urllib.parse import urlparse
import re


class PageKindMixin:
    """Methods extracted from ``YueduPlugin``."""

    def _site_markers(self) -> list[str]:
        """Return source/site names that commonly pollute scraped metadata."""
        markers = [str(self.display_name or "").strip()]
        host = urlparse(self.base_url).netloc or ""
        if host:
            markers.append(host)
            if host.lower().startswith("www."):
                markers.append(host[4:])
            hostname = host[4:] if host.lower().startswith("www.") else host
            domain_label = hostname.split(".", 1)[0].strip()
            if len(domain_label) >= 3:
                markers.append(domain_label)
        markers = [m for m in markers if len(m) >= 2]
        return sorted(set(markers), key=len, reverse=True)
    @staticmethod
    def _is_blocked_page(html: str) -> bool:
        """Detect Chinese novel-site anti-bot / captcha / rate-limit pages.

        Many sites serve a ``limit_box`` / captcha page with HTTP 200 when
        they consider the request suspicious.  Both strong single markers
        (``输入验证码后可继续访问``, ``limit_box``, ...) and weak markers paired
        with a confirmation phrase (``访问异常`` + ``验证码``) are recognized
        so the page is never persisted as chapter content.
        """
        if not html:
            return False
        lowered = html.lower()
        if any(marker in lowered for marker in STRONG_BLOCK_MARKERS):
            return True
        if has_contextual_block_marker(lowered):
            return True
        # Weak markers only count with a confirmation phrase beside them;
        # see ``has_weak_block_marker``.
        return has_weak_block_marker(lowered)
    def _captcha_hint(self) -> str:
        """Hint text for a WAF/captcha page, tailored to the source's state.

        The old wording always told the user to import a Cookie, which is
        actively misleading once a Cookie *is* configured: the page was fetched
        with it and still came back as a gate, so the Cookie is stale or bound
        to another IP/User-Agent (Cloudflare's ``cf_clearance`` is tied to both
        the address that solved the challenge and the browser's UA).

        This looks at ``_configured_cookie`` rather than ``_cookie``: the latter
        also holds session cookies the site itself sets, so a source with no
        user Cookie at all (御宅屋 hands out a ``fontsize`` preference cookie)
        was reported as "your Cookie expired".
        """
        if self._configured_cookie:
            return (
                "书源已配置 Cookie 但仍被站点拦截：Cookie 可能已过期，或与当前出口 "
                "IP / User-Agent 不匹配。请在与 NovelHub 相同的代理节点下用浏览器重新"
                "通过验证，再重新导入 Cookie；或先换一条代理线路重试"
            )
        return "请在浏览器中访问该网站通过验证后，把 Cookie 导入书源再同步"
    def _blocked_page_error(self, url: str) -> RuntimeError:
        """The "the site gated us" error, with an accurate hint."""
        return RuntimeError(
            "Site returned an anti-bot/captcha page (网站要求验证码/人机验证，"
            f"{self._captcha_hint()}): {url}"
        )
    @staticmethod
    def _looks_like_upstream_error(html: str) -> bool:
        """Detect a Cloudflare / origin 5xx error page (e.g. "Error code 520 /
        Web server is returning an unknown error").  These are transient upstream
        failures — treating them as a book with 0 chapters produced misleading
        "no usable metadata" errors and wasted the whole sync on a transient blip,
        so callers should surface this as a retryable error instead.

        The check has to stay *specific*.  The previous
        ``"cloudflare" in html and "error" in html`` test looked harmless, but
        every Cloudflare-fronted site ships
        ``static.cloudflareinsights.com/beacon.min.js`` and Blogger ships
        ``'iserror': false`` in its own JS — so ordinary posts matched it.  A
        2026-09-19 task against 中文成人文学网 (blog.xbookcn.net) failed **all 9**
        books with "transient 5xx error page" while the site was answering 200
        with the real 138 KB post.  Match Cloudflare's own wording/ids, and for
        everything else require a 5xx code next to an error word in a document
        far too small to be an article.
        """
        if not html:
            return False
        lowered = html.lower()
        if any(marker in lowered for marker in CLOUDFLARE_ERROR_IDS):
            return True
        if UPSTREAM_ERROR_CODE_RE.search(lowered):
            return True
        if len(lowered) > UPSTREAM_ERROR_PAGE_MAX_CHARS:
            return False
        if "cloudflare" in lowered and any(
            phrase in lowered for phrase in CLOUDFLARE_ERROR_PHRASES
        ):
            return True
        return bool(UPSTREAM_ERROR_BODY_RE.search(lowered))
    @staticmethod
    def _is_transient_upstream_status(status: int) -> bool:
        """Whether an HTTP status is an upstream hiccup worth retrying.

        Cloudflare answers 520-527 when its edge cannot talk to the origin and
        origins answer 500-504 of their own; both come and go within minutes.
        Treating them as retryable (instead of a WAF gate) also means
        ``sync.py`` sees the "(HTTP 52x)" signature and classifies the failure as
        transient, so a burst of them no longer counts towards
        ``SYNC_MAX_CONSECUTIVE_FAILURES`` and aborts the whole task.
        """
        return status == 429 or 500 <= status < 600
    @staticmethod
    def _looks_like_removed_page(html: str) -> bool:
        """Detect a "this novel/chapter no longer exists" notice page.

        爱丽丝书屋 answers with a 1KB page whose only text lives in JS
        (``let msg = "小说被禁用或已删除！"``) and redirects home, so the sync
        used to report the unhelpful "Chapter returned empty content".
        """
        if not html:
            return False
        lowered = html.lower()
        return any(marker in lowered for marker in REMOVED_PAGE_MARKERS)
    @classmethod
    def _is_challenge_page(cls, html: str) -> bool:
        """Detect a Cloudflare / generic JS challenge gate that may still be
        solving itself.  Unlike :meth:`_is_blocked_page`, this is lenient and
        deliberately looks for Cloudflare's own markers so we know to wait for
        the challenge (and the resulting ``cf_clearance`` cookie) to resolve.
        It deliberately does **not** fall back to the strict WAF/anti-bot
        markers: a rate-limit or captcha gate will never solve itself, so we
        only wait for JS challenges that can auto-clear.  The caller applies
        :meth:`_is_blocked_page` after the wait to reject truly blocked pages.
        """
        if not html:
            return False
        lowered = html.lower()
        # Cloudflare sets the title to "Just a moment..." while a challenge runs.
        if re.search(r"<title[^>]*>\s*just a moment", lowered):
            return True
        # Only markers that belong to a real interstitial count here.  A bare
        # ``challenge-platform`` match is Cloudflare's always-on bot-management
        # script (``/cdn-cgi/challenge-platform/scripts/jsd/main.js``), which is
        # injected into ordinary pages too; waiting 25s on it and then reporting
        # "captcha" made every Cloudflare-fronted source look blocked.
        if any(marker in lowered for marker in CF_CHALLENGE_MARKERS):
            return True
        if "browser check" in lowered:
            return True
        return False
    @classmethod
    def _content_is_blocked(cls, text: str) -> bool:
        """Reject extracted chapter text that is really an anti-bot page."""
        if not text:
            return False
        lowered = text.lower()
        strong = (
            "输入验证码后可继续访问",
            "请完成验证",
            "人机验证",
            "滑动验证",
            "验证码后可继续访问",
            "limit_box",
            "访问过于频繁",
            "请求过于频繁",
            "操作过于频繁",
        )
        if any(marker in lowered for marker in strong):
            return True
        if has_contextual_block_marker(lowered):
            return True
        if "访问异常" in lowered:
            return any(confirm in lowered for confirm in (
                "验证码", "继续访问", "稍后", "频繁", "限流",
            ))
        return False
