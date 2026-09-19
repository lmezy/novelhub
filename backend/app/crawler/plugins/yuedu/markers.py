"""Page classification: captcha gates, upstream errors, removed books.

Split out of ``plugins/yuedu/__init__.py``.  Every heuristic that
decides *what a fetched page is* -- a Cloudflare/WAF challenge, an
upstream 5xx stub, a deleted book, or ordinary prose that merely
mentions such words -- lives here, together with the windowed
confirmation logic that keeps those heuristics from firing on a
normal page.  Leaf module: it imports nothing from this package.
"""

import re


# Cloudflare challenge gates.  Every marker here is specific to a real
# interstitial: none of them appear on an ordinary page that merely loads
# Cloudflare's bot-management script (``/cdn-cgi/challenge-platform/scripts/
# jsd/main.js``).  ``challenge-platform`` and ``cf-chl`` used to sit in
# STRONG_BLOCK_MARKERS as bare substrings, which flagged *every* page of a
# Cloudflare-fronted site (御宅屋 yswhub.cc, 禁忌书屋 cool18, 搬山人 ...) as a
# captcha gate -- even ones that had already been fetched successfully with a
# valid Cookie -- and produced the misleading "please import a Cookie" error.
CF_CHALLENGE_MARKERS = (
    "just a moment",
    "managed challenge",
    "verify you are human",
    "cf-turnstile",
    "cf-challenge",
    "attention required",
    "cf_chl_opt",
    "cf-chl-",
    "chl_page",
    "challenge-form",
    "cf-please-wait",
    "浏览器安全检查",
    "正在验证您的浏览器",
    "正在检查您的浏览器",
)

STRONG_BLOCK_MARKERS = (
    "输入验证码后可继续访问",
    "验证码后可继续访问",
    "人机验证",
    "滑动验证",
    "limit_box",
    "访问过于频繁",
    "请求过于频繁",
    "操作过于频繁",
    "访问频率过高",
    "请求频率过高",
    # GoEdge WAF captcha gate (used by boluomao.com etc.)
    "goedge_waf",
    "goedge-waf",
    "请输入上面的验证码",
    # Generic WAF / challenge gates
    "waf_captcha",
    "captcha-gate",
    "verify/captcha",
    "安全网关",
    "访问被拒绝",
    "ip 已被限制",
    "ip已被限制",
    # Cloudflare / Turnstile / generic JS challenge gates, declared above so
    # this list and ``_is_challenge_page`` cannot drift apart.
    *CF_CHALLENGE_MARKERS,
)

# Phrases that are *also* ordinary Chinese prose, so a bare substring search
# over a whole page is not evidence of a gate: 風月文學網's 《隸孃》 says
# ``我的手已被限制在厚實手套中`` and the phrase used to abort that source's
# whole sync task.  They count only with a confirmation phrase beside them,
# like the weak markers below.
PROSE_GATE_MARKERS = (
    "已被限制",
    "被限制访问",
    # ``<noscript>请启用JavaScript</noscript>`` ships on plenty of ordinary
    # pages; only a gate that also asks for verification is a block.
    "请启用javascript",
    "请开启javascript",
)

# Weak markers need a confirmation phrase to avoid false positives on
# normal pages (e.g. a login dialog mentioning a captcha code).
WEAK_BLOCK_MARKERS = (
    "访问异常",
    "访问频繁",
    "请求频繁",
    "限流",
)

# Phrases that confirm a weak marker.  They are only trusted when they sit
# right next to the marker: a bare substring search over the whole document
# made 御宅屋 (yswhub.cc) look rate-limited because its sidebar listed a novel
# called 《限流情缘一线牵》 while Cloudflare's always-on bot-management snippet
# (``/cdn-cgi/challenge-platform/scripts/jsd/main.js``) put the word
# "challenge" 800 characters further down the page.
WEAK_BLOCK_CONFIRMATIONS = (
    "验证码", "继续访问", "稍后再试", "后再试", "限流",
    # Not the bare "频繁": ordinary narration ("他频繁出入") would confirm a
    # marker that merely shares the word.  Rate-limit gates spell out
    # "访问过于频繁" / "请求频繁" instead.
    "访问频繁", "请求频繁", "操作频繁", "过于频繁",
    "captcha", "challenge", "security",
)

# How far away a weak marker's confirmation may sit (either side).
WEAK_BLOCK_WINDOW = 120

# Phrases that also occur in ordinary site chrome -- 禁忌书屋's report button
# ships `alert('举报失败，请稍后再试')`, which used to flag every thread page as
# a captcha gate.  They only count when a verification/rate-limit word sits
# next to them.
CONTEXTUAL_BLOCK_MARKERS = (
    "请稍后再试",
    "请稍后重试",
    "请完成验证",
    "安全验证",
    "身份验证",
    *PROSE_GATE_MARKERS,
)
CONTEXTUAL_BLOCK_HINTS = (
    "验证码",
    # ``人机`` alone matched 无人机 ("drone") in 禁忌书屋's novel text and
    # flagged a perfectly normal thread page as a captcha gate.
    "人机验证",
    # ``频繁`` alone matched ordinary narration; the gate always spells out
    # what is too frequent.
    "访问频繁",
    "请求频繁",
    "操作频繁",
    "过于频繁",
    "限流",
    "访问异常",
    "访问被拒绝",
    "自动程序",
    "安全服务",
    "拦截",
    "限制访问",
    "已被限制",
    "captcha",
    "challenge",
)

# Notice pages for content that no longer exists.  Sites answer with a tiny
# page (often only a JS alert) instead of a 404, which otherwise surfaces as
# the unhelpful "Chapter returned empty content".
REMOVED_PAGE_MARKERS = (
    "小说被禁用或已删除",
    "作品被禁用或已删除",
    "小说已删除",
    "作品已删除",
    "书籍已删除",
    "该作品已被删除",
    "内容已被删除",
    "小说不存在",
    "作品不存在",
    "书籍不存在",
    "该作品已下架",
    "作品已下架",
    "小说已下架",
)


# Ids Cloudflare only emits on its own error pages ("Error 520 / Web server is
# returning an unknown error", 521-527).  They never appear on a healthy page.
CLOUDFLARE_ERROR_IDS = (
    "cf-error-details",
    "cf-error-overview",
    "cf-error-code",
)

# Cloudflare names the failure inside the error page itself: 520 "Web server is
# returning an unknown error", 521 "Web server is down", 522 "Connection timed
# out", 523 "Origin is unreachable", 524 "A timeout occurred", 525/526 TLS.
CLOUDFLARE_ERROR_PHRASES = (
    "web server is returning an unknown error",
    "web server is down",
    "connection timed out",
    "origin is unreachable",
    "a timeout occurred",
    "ssl handshake failed",
    "invalid ssl certificate",
)

# Cloudflare's *plain text* 5xx body is literally ``error code: 520``.
UPSTREAM_ERROR_CODE_RE = re.compile(r"error\s+code:?\s*5\d{2}\b")

# Origin/nginx style error pages: a 5xx code and the word "error" next to each
# other.  Deliberately paired with :data:`UPSTREAM_ERROR_PAGE_MAX_CHARS` so a
# real article that happens to contain such a phrase cannot be mistaken for an
# error page.
UPSTREAM_ERROR_BODY_RE = re.compile(
    r"(?:\berror\b|错误)[^<]{0,40}\b5\d{2}\b"
    r"|\b5\d{2}\b[^<]{0,40}(?:bad gateway|service unavailable"
    r"|internal server error|gateway time-?out|错误)"
)

# An upstream error page is a stub (Cloudflare's own is ~7 KB); a real book or
# chapter page is far bigger.  Anything above this is never treated as an error
# page on wording alone.
UPSTREAM_ERROR_PAGE_MAX_CHARS = 30000


def has_contextual_block_marker(text: str) -> bool:
    """Whether an ambiguous block phrase appears in a blocking context.

    The confirmation is looked up in a window on either side of the marker.  A
    phrase that is itself listed as both a marker and a confirmation is skipped
    for its own occurrence (it cannot confirm itself), which otherwise put
    every page that merely mentions it back in the "captcha" bucket (風月文學網's
    《隸孃》 says ``我的手已被限制在厚實手套中``).
    """
    lowered = str(text or "").lower()
    if not lowered:
        return False
    for marker in CONTEXTUAL_BLOCK_MARKERS:
        start = 0
        while True:
            index = lowered.find(marker, start)
            if index == -1:
                break
            window = lowered[max(0, index - 90): index + len(marker) + 90]
            for hint in CONTEXTUAL_BLOCK_HINTS:
                if hint == marker:
                    continue
                if hint in window:
                    return True
            start = index + len(marker)
    return False


def has_weak_block_marker(text: str) -> bool:
    """Whether a vague rate-limit phrase is confirmed right next to it.

    The confirmation is looked up in a small window on either side of the
    marker (and never inside the marker itself, which otherwise confirms
    itself).  Searching the whole document instead paired a novel title that
    happened to contain "限流" with an unrelated "challenge" string in a
    Cloudflare script far away, which aborted whole 御宅屋 sync tasks.
    """
    lowered = str(text or "").lower()
    if not lowered:
        return False
    for marker in WEAK_BLOCK_MARKERS:
        start = 0
        while True:
            index = lowered.find(marker, start)
            if index == -1:
                break
            start = index + len(marker)
            window = (
                lowered[max(0, index - WEAK_BLOCK_WINDOW): index]
                + lowered[start: start + WEAK_BLOCK_WINDOW]
            )
            if any(
                confirmation in window
                for confirmation in WEAK_BLOCK_CONFIRMATIONS
            ):
                return True
    return False
