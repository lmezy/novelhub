"""YueDu book source import API.

Endpoints:
- POST /api/yuedu/import   Import yuedu sources from URL or raw JSON
- GET  /api/yuedu/preview  Preview what sources a URL would import
"""

import asyncio
import hashlib
import json
import re
from typing import Any
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

from bs4 import BeautifulSoup

import httpx
from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import CrawlTask, Source, User
from app.services.auth import get_current_user
from app.services.proxy_config import get_proxy_config
from app.services.task_queue import enqueue_crawl_all

router = APIRouter(prefix="/yuedu", tags=["yuedu"])


async def _fetch_response(
    url: str,
    timeout: float = 30.0,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    cfg = get_proxy_config()
    proxy_url = (cfg.https_proxy or cfg.http_proxy) if cfg.enabled else None
    proxies: list[str | None] = [None]
    if proxy_url:
        proxies.insert(0, proxy_url)

    last_error: httpx.HTTPError | None = None
    for proxy in proxies:
        try:
            async with httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=True,
                proxy=proxy,
                trust_env=False,
            ) as client:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                return resp
        except httpx.RequestError as exc:
            last_error = exc
            if proxy is None:
                raise
            logger.warning(
                "Configured proxy {} unreachable ({}); retrying direct",
                proxy_url,
                exc,
            )

    if last_error is not None:
        raise last_error
    raise httpx.ConnectError(f"Request failed for {url}")


_YCKCEO_HOSTS = ("yckceo.com", "yckceo.vip")
_YCKCEO_LISTING_PATH = "/yuedu/shuyuan/index.html"
_YCKCEO_CONTENT_ID_RE = re.compile(
    r"/yuedu/shuyuan/content/id/(\d+)\.html",
    re.IGNORECASE,
)


def _normalize_import_url(raw_url: str) -> str:
    """Accept yckceo/legado one-click links and plain http(s) URLs."""
    url = raw_url.strip()
    if url.endswith("#requestWithoutUA"):
        url = url[: -len("#requestWithoutUA")]
    if url.lower().startswith(("yuedu://", "legado://")):
        if "?" not in url:
            raise ValueError("Import link must contain a src parameter")
        params = parse_qs(url.split("?", 1)[1])
        src = (params.get("src") or [""])[0]
        if not src:
            raise ValueError("Import link has no src parameter")
        return unquote(src)
    if url.startswith(("http://", "https://")):
        return url
    raise ValueError(
        "Only http(s), yuedu:// or legado:// import URLs are supported"
    )


def _has_no_ua_flag(raw_url: str) -> bool:
    return raw_url.strip().endswith("#requestWithoutUA")


def _is_yckceo_host(host: str | None) -> bool:
    lowered = (host or "").lower()
    return any(
        lowered == item or lowered.endswith("." + item)
        for item in _YCKCEO_HOSTS
    )


def _source_last_update(config: Any) -> int:
    if not isinstance(config, dict):
        return 0
    try:
        return int(config.get("lastUpdateTime", 0))
    except (TypeError, ValueError):
        return 0


def _dedupe_sources(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for src in sources:
        if not isinstance(src, dict):
            continue
        src = _normalize_source_config(src)
        url = str(src.get("bookSourceUrl", "") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        result.append(src)
    return result


def _normalize_source_config(src: dict[str, Any]) -> dict[str, Any]:
    """Convert legacy yckceo/Legado source shapes to the current rule model."""
    if "searchList" in src:
        result = _convert_very_old_source(src)
    elif any(
        key in src
        for key in (
            "ruleSearchUrl",
            "ruleFindUrl",
            "ruleSearchList",
            "ruleFindList",
            "ruleBookName",
            "ruleBookContent",
            "ruleChapterList",
            "ruleContentUrl",
        )
    ):
        result = _convert_rule_flat_source(src)
    else:
        result = _rename_legacy_v3_source(src)
    return _normalize_rule_objects(result)


def _normalize_rule_objects(src: dict[str, Any]) -> dict[str, Any]:
    """Empty rule arrays from yckceo exports become empty dicts for the engine."""
    for key in (
        "ruleSearch",
        "ruleExplore",
        "ruleBookInfo",
        "ruleToc",
        "ruleContent",
    ):
        value = src.get(key)
        if isinstance(value, dict):
            continue
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                src[key] = parsed
                continue
        src[key] = {}

    book_info = src.get("ruleBookInfo")
    if isinstance(book_info, dict):
        if "cover" in book_info and "coverUrl" not in book_info:
            book_info["coverUrl"] = book_info.pop("cover")
        if "catalogUrl" in book_info and "tocUrl" not in book_info:
            book_info["tocUrl"] = book_info.pop("catalogUrl")

    content_rules = src.get("ruleContent")
    if isinstance(content_rules, dict):
        if "nextContent" in content_rules and "nextContentUrl" not in content_rules:
            content_rules["nextContentUrl"] = content_rules.pop("nextContent")
        if "filter" in content_rules and "replaceRegex" not in content_rules:
            content_rules["replaceRegex"] = content_rules.pop("filter")
    return src


def _rename_legacy_v3_source(src: dict[str, Any]) -> dict[str, Any]:
    """Map the old v3 field names used by some yckceo exports to current ones."""
    if not any(
        key in src
        for key in (
            "searchRule",
            "exploreRule",
            "bookInfoRule",
            "tocRule",
            "contentRule",
            "bookListRule",
            "loginHeader",
        )
    ) and "enable" not in src:
        return src

    out = dict(src)
    renames = {
        "searchRule": "ruleSearch",
        "exploreRule": "ruleExplore",
        "bookInfoRule": "ruleBookInfo",
        "tocRule": "ruleToc",
        "contentRule": "ruleContent",
    }
    for old, new in renames.items():
        if old in out and new not in out:
            out[new] = out.pop(old)
    if "bookListRule" in out:
        book_list_rule = out.pop("bookListRule")
        if isinstance(book_list_rule, dict):
            if "ruleSearch" not in out:
                out["ruleSearch"] = book_list_rule
            elif "ruleExplore" not in out:
                out["ruleExplore"] = book_list_rule
    if "loginHeader" in out and not out.get("header"):
        out["header"] = out.pop("loginHeader")
    if "enable" in out and "enabled" not in out:
        out["enabled"] = out.pop("enable")
    return out


_LEGACY_RULE_KEYS = {
    "ruleSearchUrl",
    "ruleFindUrl",
    "ruleSearchList",
    "ruleSearchName",
    "ruleSearchAuthor",
    "ruleSearchIntroduce",
    "ruleSearchKind",
    "ruleSearchNoteUrl",
    "ruleSearchCoverUrl",
    "ruleSearchLastChapter",
    "ruleFindList",
    "ruleFindName",
    "ruleFindAuthor",
    "ruleFindIntroduce",
    "ruleFindKind",
    "ruleFindNoteUrl",
    "ruleFindCoverUrl",
    "ruleFindLastChapter",
    "ruleBookInfoInit",
    "ruleBookName",
    "ruleBookAuthor",
    "ruleIntroduce",
    "ruleBookKind",
    "ruleCoverUrl",
    "ruleBookLastChapter",
    "ruleChapterUrl",
    "ruleChapterList",
    "ruleChapterName",
    "ruleContentUrl",
    "ruleChapterUrlNext",
    "ruleBookContent",
    "ruleBookContentReplace",
    "ruleContentUrlNext",
    "ruleBookUrlPattern",
    "httpUserAgent",
    "serialNumber",
}


def _rule_dict(**kwargs: Any) -> dict[str, Any]:
    return {key: value for key, value in kwargs.items() if value is not None}


def _to_new_rule(old_rule: Any) -> str | None:
    """Ported from ImportOldData.toNewRule in the yuedu codebase."""
    if old_rule is None:
        return None
    old_rule = str(old_rule).strip()
    if not old_rule:
        return None

    new_rule = old_rule
    reverse = False
    allinone = False
    if new_rule.startswith("-"):
        reverse = True
        new_rule = new_rule[1:]
    if new_rule.startswith("+"):
        allinone = True
        new_rule = new_rule[1:]

    lowered = new_rule.lower()
    needs_adaptation = not (
        lowered.startswith("@css:")
        or lowered.startswith("@xpath:")
        or new_rule.startswith("//")
        or new_rule.startswith("##")
        or new_rule.startswith(":")
        or "@js:" in lowered
        or "<js>" in lowered
    )
    if needs_adaptation:
        if "#" in new_rule and "##" not in new_rule:
            new_rule = new_rule.replace("#", "##")
        if "|" in new_rule and "||" not in new_rule:
            if "##" in new_rule:
                parts = new_rule.split("##")
                if "|" in parts[0]:
                    new_rule = parts[0].replace("|", "||")
                    for item in parts[1:]:
                        new_rule += "##" + item
            else:
                new_rule = new_rule.replace("|", "||")
        if (
            "&" in new_rule
            and "&&" not in new_rule
            and "http" not in lowered
            and not new_rule.startswith("/")
        ):
            new_rule = new_rule.replace("&", "&&")

    if allinone:
        new_rule = "+" + new_rule
    if reverse:
        new_rule = "-" + new_rule
    return new_rule


_HEADER_PATTERN = re.compile(r"@Header:\{.+?\}", re.IGNORECASE)
_JS_PATTERN = re.compile(r"\{\{.+?\}\}", re.IGNORECASE)


def _to_new_url(old_url: Any) -> str | None:
    """Ported from ImportOldData.toNewUrl in the yuedu codebase."""
    if old_url is None:
        return None
    url = str(old_url).strip()
    if not url:
        return None
    if url.lower().startswith("<js>"):
        return (
            url.replace("=searchKey", "={{key}}")
            .replace("=searchPage", "={{page}}")
        )

    placeholders = {
        "{{key}}": "\x00key\x00",
        "{{page}}": "\x00page\x00",
        "{{page+1}}": "\x00page+1\x00",
        "{{page-1}}": "\x00page-1\x00",
    }
    for old, sentinel in placeholders.items():
        url = url.replace(old, sentinel)

    options: dict[str, Any] = {}
    header_match = _HEADER_PATTERN.search(url)
    if header_match:
        header = header_match.group()
        url = url.replace(header, "", 1)
        options["headers"] = header[len("@Header:") :]

    url_parts = url.split("|")
    url = url_parts[0]
    if len(url_parts) > 1 and "=" in url_parts[1]:
        options["charset"] = url_parts[1].split("=", 1)[1]

    js_list: list[str] = []
    for js_match in _JS_PATTERN.finditer(url):
        js_list.append(js_match.group())
        url = url.replace(js_list[-1], "${%d}" % (len(js_list) - 1), 1)

    url = url.replace("{", "<").replace("}", ">")
    url = url.replace("searchKey", "{{key}}")
    url = re.sub(r"<searchPage([-+]1)>", r"{{page\1}}", url)
    url = re.sub(r"searchPage([-+]1)", r"{{page\1}}", url)
    url = url.replace("searchPage", "{{page}}")
    for index, item in enumerate(js_list):
        url = url.replace(
            "${%d}" % index,
            item.replace("searchKey", "key").replace("searchPage", "page"),
        )

    url_parts = url.split("@")
    url = url_parts[0]
    if len(url_parts) > 1:
        options["method"] = "POST"
        options["body"] = "@".join(url_parts[1:])
    if options:
        url += "," + json.dumps(options, ensure_ascii=False, separators=(",", ":"))
    for old, sentinel in placeholders.items():
        url = url.replace(sentinel, old)
    return url


def _to_new_urls(old_urls: Any) -> str | None:
    """Ported from ImportOldData.toNewUrls in the yuedu codebase."""
    if old_urls is None:
        return None
    urls_text = str(old_urls).strip()
    if not urls_text:
        return None
    if urls_text.startswith("@js:") or urls_text.startswith("<js>"):
        return urls_text
    if "\n" not in urls_text and "&&" not in urls_text:
        return _to_new_url(urls_text)

    converted: list[str] = []
    for item in re.split(r"(?:&&|\r?\n)+", urls_text):
        new_url = _to_new_url(item)
        if new_url:
            converted.append(re.sub(r"\n\s*", "", new_url))
    return "\n".join(converted) if converted else None


def _ua_to_header(user_agent: Any) -> str | None:
    if not user_agent:
        return None
    return json.dumps(
        {"User-Agent": str(user_agent)},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _convert_rule_flat_source(src: dict[str, Any]) -> dict[str, Any]:
    """Ported from ImportOldData.fromOldBookSource in the yuedu codebase."""
    out = {
        key: value
        for key, value in src.items()
        if key not in _LEGACY_RULE_KEYS
    }
    out["bookSourceUrl"] = str(src.get("bookSourceUrl", "") or "")
    out["bookSourceName"] = str(src.get("bookSourceName", "") or "")
    for key in (
        "bookSourceGroup",
        "loginUrl",
        "loginUi",
        "loginCheckJs",
        "coverDecodeJs",
        "bookSourceComment",
    ):
        if src.get(key) is not None:
            out[key] = src[key]
    if src.get("ruleBookUrlPattern"):
        out["bookUrlPattern"] = src["ruleBookUrlPattern"]
    if src.get("serialNumber") is not None:
        out["customOrder"] = src["serialNumber"]
    header = _ua_to_header(src.get("httpUserAgent"))
    if header:
        out["header"] = header
    search_url = _to_new_url(src.get("ruleSearchUrl"))
    if search_url:
        out["searchUrl"] = search_url
    explore_url = _to_new_urls(src.get("ruleFindUrl"))
    if explore_url:
        out["exploreUrl"] = explore_url
    out["bookSourceType"] = (
        1 if str(src.get("bookSourceType", "")).upper() == "AUDIO" else 0
    )
    out["enabled"] = src.get("enable", True)
    out["enabledExplore"] = bool(explore_url)
    out.pop("enable", None)
    out["ruleSearch"] = _rule_dict(
        bookList=_to_new_rule(src.get("ruleSearchList")),
        name=_to_new_rule(src.get("ruleSearchName")),
        author=_to_new_rule(src.get("ruleSearchAuthor")),
        intro=_to_new_rule(src.get("ruleSearchIntroduce")),
        kind=_to_new_rule(src.get("ruleSearchKind")),
        bookUrl=_to_new_rule(src.get("ruleSearchNoteUrl")),
        coverUrl=_to_new_rule(src.get("ruleSearchCoverUrl")),
        lastChapter=_to_new_rule(src.get("ruleSearchLastChapter")),
    )
    out["ruleExplore"] = _rule_dict(
        bookList=_to_new_rule(src.get("ruleFindList")),
        name=_to_new_rule(src.get("ruleFindName")),
        author=_to_new_rule(src.get("ruleFindAuthor")),
        intro=_to_new_rule(src.get("ruleFindIntroduce")),
        kind=_to_new_rule(src.get("ruleFindKind")),
        bookUrl=_to_new_rule(src.get("ruleFindNoteUrl")),
        coverUrl=_to_new_rule(src.get("ruleFindCoverUrl")),
        lastChapter=_to_new_rule(src.get("ruleFindLastChapter")),
    )
    out["ruleBookInfo"] = _rule_dict(
        init=_to_new_rule(src.get("ruleBookInfoInit")),
        name=_to_new_rule(src.get("ruleBookName")),
        author=_to_new_rule(src.get("ruleBookAuthor")),
        intro=_to_new_rule(src.get("ruleIntroduce")),
        kind=_to_new_rule(src.get("ruleBookKind")),
        coverUrl=_to_new_rule(src.get("ruleCoverUrl")),
        lastChapter=_to_new_rule(src.get("ruleBookLastChapter")),
        tocUrl=_to_new_rule(src.get("ruleChapterUrl")),
    )
    out["ruleToc"] = _rule_dict(
        chapterList=_to_new_rule(src.get("ruleChapterList")),
        chapterName=_to_new_rule(src.get("ruleChapterName")),
        chapterUrl=_to_new_rule(src.get("ruleContentUrl")),
        nextTocUrl=_to_new_rule(src.get("ruleChapterUrlNext")),
    )
    content = _to_new_rule(src.get("ruleBookContent")) or ""
    if content.startswith("$") and not content.startswith("$."):
        content = content[1:]
    out["ruleContent"] = _rule_dict(
        content=content or None,
        replaceRegex=_to_new_rule(src.get("ruleBookContentReplace")),
        nextContentUrl=_to_new_rule(src.get("ruleContentUrlNext")),
    )
    return out


def _convert_very_old_source(src: dict[str, Any]) -> dict[str, Any]:
    """Best-effort conversion for old 2.x sources exported by yckceo."""
    out = dict(src)
    if "enable" in out and "enabled" not in out:
        out["enabled"] = out.pop("enable")
    if "headers" in out and isinstance(out["headers"], dict) and not out.get("header"):
        out["header"] = json.dumps(
            out.pop("headers"),
            ensure_ascii=False,
            separators=(",", ":"),
        )

    search_url = _to_new_url(out.get("searchUrl"))
    if search_url:
        out["searchUrl"] = search_url
    out["ruleSearch"] = _rule_dict(
        bookList=_to_new_rule(out.get("searchList")),
        name=_to_new_rule(out.get("searchName")),
        author=_to_new_rule(out.get("searchAuthor")),
        intro=_to_new_rule(out.get("searchIntro")),
        coverUrl=_to_new_rule(out.get("searchCover")),
        kind=_to_new_rule(out.get("searchKind")),
        bookUrl=_to_new_rule(out.get("searchDetailUrl")),
    )

    book_info = out.get("bookInfoRule")
    if isinstance(book_info, dict):
        toc_url = _to_new_rule(book_info.get("catalogUrl")) or _to_new_rule(
            out.get("bookDetailUrl")
        )
        out["ruleBookInfo"] = _rule_dict(
            init=_to_new_rule(book_info.get("init")),
            name=_to_new_rule(book_info.get("name")),
            author=_to_new_rule(book_info.get("author")),
            intro=_to_new_rule(book_info.get("intro")),
            coverUrl=_to_new_rule(book_info.get("cover")),
            lastChapter=_to_new_rule(book_info.get("lastChapter")),
            tocUrl=toc_url,
        )
    elif out.get("bookDetailUrl"):
        out["ruleBookInfo"] = _rule_dict(
            tocUrl=_to_new_rule(out.get("bookDetailUrl")),
        )

    catalog = out.get("catalogRule")
    if isinstance(catalog, dict):
        out["ruleToc"] = _rule_dict(
            chapterList=_to_new_rule(catalog.get("chapterList")),
            chapterName=_to_new_rule(catalog.get("chapterName")),
            chapterUrl=_to_new_rule(catalog.get("chapterUrl")),
        )

    content = out.get("contentRule")
    if isinstance(content, dict):
        raw_content = _to_new_rule(content.get("content"))
        if raw_content and raw_content.startswith("$") and not raw_content.startswith("$."):
            raw_content = raw_content[1:]
        out["ruleContent"] = _rule_dict(
            content=raw_content,
            nextContentUrl=_to_new_rule(content.get("nextContent")),
            replaceRegex=content.get("filter") or out.get("contentReplace"),
        )

    for key in (
        "searchList",
        "searchName",
        "searchAuthor",
        "searchCover",
        "searchIntro",
        "searchKind",
        "searchDetailUrl",
        "bookDetailUrl",
        "searchUrlNext",
        "contentReplace",
        "bookInfoRule",
        "catalogRule",
        "contentRule",
    ):
        out.pop(key, None)
    return out


def _parse_yckceo_listing_ids(soup: BeautifulSoup) -> list[str]:
    ids: list[str] = []
    for node in soup.select('input[name="ids[]"]'):
        value = (node.get("value") or "").strip()
        if value and value not in ids:
            ids.append(value)
    return ids


def _parse_yckceo_content_ids(soup: BeautifulSoup) -> list[str]:
    ids: list[str] = []
    for node in soup.select('a[href*="/yuedu/shuyuan/content/id/"]'):
        match = _YCKCEO_CONTENT_ID_RE.search(node.get("href", ""))
        if match and match.group(1) not in ids:
            ids.append(match.group(1))
    return ids


def _collect_import_urls(soup: BeautifulSoup, page_url: str) -> list[str]:
    urls: list[str] = []
    json_input = soup.select_one("#jsonurl")
    if json_input:
        value = (json_input.get("value") or "").strip()
        if value:
            urls.append(value)
    for node in soup.select("a[href]"):
        href = (node.get("href") or "").strip()
        if not href:
            continue
        lowered = href.lower()
        if lowered.startswith(("yuedu://", "legado://")):
            urls.append(href)
        elif any(marker in lowered for marker in (".json", "/json/", "jsons")):
            urls.append(urljoin(page_url, href))

    seen: set[str] = set()
    result: list[str] = []
    for url in urls:
        try:
            normalized = _normalize_import_url(url)
        except ValueError:
            continue
        if normalized not in seen:
            seen.add(normalized)
            result.append(url)
    return result


async def _fetch_yckceo_jsons(
    ids: list[str],
    page_url: str,
) -> list[dict[str, Any]]:
    parts = urlsplit(page_url)
    base = f"{parts.scheme}://{parts.netloc}"
    sources: list[dict[str, Any]] = []
    for index in range(0, len(ids), 100):
        chunk = ids[index : index + 100]
        jsons_url = f"{base}/yuedu/shuyuan/jsons?id={'-'.join(chunk)}"
        resp = await _fetch_response(jsons_url)
        try:
            parsed = json.loads(resp.text)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"yckceo returned invalid JSON for {jsons_url}: {exc}"
            ) from exc
        sources.extend(_extract_sources(parsed))
    return _dedupe_sources(sources)


async def _load_sources_from_html(
    text: str,
    page_url: str,
) -> list[dict[str, Any]]:
    soup = BeautifulSoup(text, "lxml")

    # Detail pages embed the whole BookSource inside a <pre id="jsonpre">.
    for node in soup.select("#jsonpre, pre, textarea"):
        inner = (node.get_text() or "").strip()
        if not inner:
            continue
        try:
            parsed = json.loads(inner)
        except json.JSONDecodeError:
            continue
        sources = _extract_sources(parsed)
        if sources:
            return sources

    # Generic repositories usually expose a JSON URL or one-click import link.
    import_urls = _collect_import_urls(soup, page_url)
    if import_urls:
        sources: list[dict[str, Any]] = []
        for child_url in import_urls:
            sources.extend(await _fetch_sources_from_url(child_url))
        if sources:
            return _dedupe_sources(sources)

    parts = urlsplit(page_url)
    if (
        _is_yckceo_host(parts.hostname)
        and parts.path.lower() == _YCKCEO_LISTING_PATH
    ):
        ids = _parse_yckceo_listing_ids(soup)
        if not ids:
            ids = _parse_yckceo_content_ids(soup)
        if ids:
            return await _fetch_yckceo_jsons(ids, page_url)

    return []


async def _load_sources_from_text(text: str) -> list[dict[str, Any]]:
    stripped = text.strip()
    if stripped.startswith(("http://", "https://", "yuedu://", "legado://")):
        return await _fetch_sources_from_url(stripped)
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return await _load_sources_from_html(stripped, "")

    if isinstance(parsed, dict) and isinstance(parsed.get("sourceUrls"), list):
        sources: list[dict[str, Any]] = []
        for child_url in parsed["sourceUrls"]:
            if isinstance(child_url, str) and child_url.strip():
                sources.extend(await _fetch_sources_from_url(child_url))
        return _dedupe_sources(sources)

    return _dedupe_sources(_extract_sources(parsed))


async def _fetch_sources_from_url(url: str) -> list[dict[str, Any]]:
    no_ua = _has_no_ua_flag(url)
    normalized = _normalize_import_url(url)
    resp = await _fetch_response(
        normalized,
        headers={"User-Agent": "null"} if no_ua else None,
    )
    try:
        parsed = json.loads(resp.text)
    except json.JSONDecodeError:
        return await _load_sources_from_html(resp.text, str(resp.url))

    if isinstance(parsed, dict) and isinstance(parsed.get("sourceUrls"), list):
        sources: list[dict[str, Any]] = []
        for child_url in parsed["sourceUrls"]:
            if isinstance(child_url, str) and child_url.strip():
                sources.extend(await _fetch_sources_from_url(child_url))
        return _dedupe_sources(sources)

    return _dedupe_sources(_extract_sources(parsed))


class YueduImportRequest(BaseModel):
    url: str | None = None
    json_text: str | None = None
    is_r18: bool = False
    scope: str = "personal"


class YueduImportResult(BaseModel):
    total: int
    imported: int
    skipped: int
    updated: int = 0
    sources: list[dict[str, Any]]


@router.post("/import", response_model=YueduImportResult)
async def import_yuedu_sources(
    payload: YueduImportRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Import YueDu book sources from a URL or raw JSON text."""
    scope = payload.scope or "personal"
    if scope == "global" and user.role not in ("admin", "super_admin"):
        raise HTTPException(status_code=403, detail="Only admins can import global sources")
    owner_id = None if scope == "global" else user.id
    sources_json: list[dict[str, Any]] = []

    if payload.json_text:
        try:
            sources_json = await _load_sources_from_text(payload.json_text)
        except json.JSONDecodeError as e:
            raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")
        except httpx.HTTPError as e:
            raise HTTPException(status_code=502, detail=f"Failed to fetch source URL: {e}")
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    elif payload.url:
        try:
            sources_json = await _fetch_sources_from_url(payload.url)
        except httpx.HTTPError as e:
            raise HTTPException(status_code=502, detail=f"Failed to fetch URL: {e}")
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except json.JSONDecodeError as e:
            raise HTTPException(status_code=400, detail=f"URL returned invalid JSON: {e}")

    else:
        raise HTTPException(status_code=400, detail="Either url or json_text is required")

    if not sources_json:
        raise HTTPException(status_code=400, detail="No valid book sources found in the input")

    total = len(sources_json)
    imported = 0
    updated = 0
    skipped = 0
    results: list[dict[str, Any]] = []

    for src in sources_json:
        name = src.get("bookSourceName", "Unknown")
        base_url = src.get("bookSourceUrl", "")
        source_id = _make_source_id(name, base_url, owner_id)

        existing = await db.get(Source, source_id)
        if existing:
            remote_ts = _source_last_update(src)
            local_ts = _source_last_update(existing.config)
            if remote_ts > local_ts:
                existing.name = name
                existing.url = base_url
                existing.config = src
                existing.is_r18 = payload.is_r18
                existing.owner_id = owner_id
                db.add(existing)
                updated += 1
                results.append({"id": source_id, "name": name, "status": "updated"})
            else:
                skipped += 1
                results.append({"id": source_id, "name": name, "status": "skipped"})
            continue

        source = Source(
            id=source_id,
            name=name,
            url=base_url,
            plugin_name="yuedu",
            enabled=True,
            is_r18=payload.is_r18,
            config=src,
            owner_id=owner_id,
        )
        db.add(source)
        imported += 1
        results.append({"id": source_id, "name": name, "status": "imported", "bookshelf_url": None})

    await db.commit()

    # Bookshelf URLs are detected lazily during sync, so import stays fast.

    return YueduImportResult(
        total=total,
        imported=imported,
        skipped=skipped,
        updated=updated,
        sources=results,
    )


class YueduImportSyncRequest(BaseModel):
    url: str | None = None
    json_text: str | None = None
    is_r18: bool = False
    cookie: str | None = None
    discover: bool = True
    max_discover_pages: int = 3
    scope: str = "personal"


class YueduImportSyncResult(BaseModel):
    sources_total: int
    sources_imported: int
    sources_skipped: int
    sources_updated: int = 0
    books_synced: int
    chapters_downloaded: int
    books_discovered: int
    errors: list[dict[str, Any]]
    details: list[dict[str, Any]]


class YueduImportTaskResult(BaseModel):
    tasks: list[dict[str, Any]]


@router.post("/import-task", response_model=YueduImportTaskResult, status_code=202)
async def import_yuedu_sources_as_tasks(
    payload: YueduImportSyncRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Import sources and enqueue one crawl task per source for progress tracking."""
    from uuid import uuid4

    from app.models import Cookie
    from app.services.cookie_crypto import encrypt_cookie

    import_result = await import_yuedu_sources(
        YueduImportRequest(
            url=payload.url,
            json_text=payload.json_text,
            is_r18=payload.is_r18,
            scope=payload.scope,
        ),
        user,
        db,
    )

    if payload.cookie and payload.cookie.strip():
        for src_info in import_result.sources:
            source_id = src_info["id"]
            existing_cookie = await db.scalar(
                select(Cookie).where(Cookie.source == source_id)
            )
            if existing_cookie is None:
                db.add(Cookie(
                    id=str(uuid4()),
                    source=source_id,
                    cookie_data=encrypt_cookie(payload.cookie.strip()),
                ))
        await db.commit()

    tasks: list[dict[str, Any]] = []
    if payload.discover:
        for src_info in import_result.sources:
            task = CrawlTask(
                id=str(uuid4()),
                source=src_info["id"],
                mode="discover_all",
                max_pages=payload.max_discover_pages,
                status="pending",
                user_id=user.id,
            )
            db.add(task)
            await db.flush()
            enqueue_crawl_all(task.source, task.max_pages, task.id)
            tasks.append({
                "id": task.id,
                "task_id": task.id,
                "source_id": task.source,
                "source_name": src_info.get("name", task.source),
                "status": task.status,
                "max_pages": task.max_pages,
                "mode": task.mode,
                "started_at": task.started_at,
                "finished_at": task.finished_at,
                "error": task.error,
                "result": task.result,
                "progress": task.progress,
                "created_at": task.created_at,
            })
        await db.commit()

    return YueduImportTaskResult(tasks=tasks)


@router.post("/import-and-sync", response_model=YueduImportSyncResult)
async def import_and_sync_all(
    payload: YueduImportSyncRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Import yuedu sources and immediately sync all discovered books.

    This is the primary one-click workflow:
    1. Import all book sources from the URL/JSON
    2. If cookie provided, save it for each source
    3. For each source, sync bookshelf (needs cookie)
    4. For each source, discover books from explore/category pages
    5. Return comprehensive results
    """
    from uuid import uuid4
    from app.models import Cookie
    from app.services.cookie_crypto import encrypt_cookie
    from app.services.sync import SyncService

    # Step 1: Import sources
    import_result = await import_yuedu_sources(
        YueduImportRequest(
            url=payload.url,
            json_text=payload.json_text,
            is_r18=payload.is_r18,
            scope=payload.scope,
        ),
        user,
        db,
    )

    result = YueduImportSyncResult(
        sources_total=import_result.total,
        sources_imported=import_result.imported,
        sources_skipped=import_result.skipped,
        sources_updated=import_result.updated,
        books_synced=0,
        chapters_downloaded=0,
        books_discovered=0,
        errors=[],
        details=[],
    )

    # Step 2: Save cookie if provided
    if payload.cookie and payload.cookie.strip():
        for src_info in import_result.sources:
            source_id = src_info["id"]
            existing_cookie = await db.scalar(
                select(Cookie).where(Cookie.source == source_id)
            )
            if existing_cookie is None:
                cookie_obj = Cookie(
                    id=str(uuid4()),
                    source=source_id,
                    cookie_data=encrypt_cookie(payload.cookie.strip()),
                )
                db.add(cookie_obj)
        await db.commit()

    # Step 3 & 4: Sync bookshelf + discover for each imported source in parallel
    from app.core.config import settings, sync_thread_count
    from app.core.database import SessionLocal

    async def _sync_source(src_info):
        source_id = src_info["id"]
        source_name = src_info.get("name", source_id)
        detail = {"source_id": source_id, "name": source_name, "sync": {}, "discover": {}}
        errors: list[dict[str, Any]] = []
        async with SessionLocal() as session:
            sync_service = SyncService(session)
            try:
                cookie_record = await session.scalar(
                    select(Cookie).where(Cookie.source == source_id)
                )
                if cookie_record:
                    shelf_result = await sync_service.sync_bookshelf(source_id)
                    synced = sum(
                        r.get("created_chapters", 0)
                        for r in shelf_result.get("results", [])
                        if isinstance(r, dict)
                    )
                    detail["sync"] = {
                        "books_found": shelf_result.get("total", 0),
                        "chapters_downloaded": synced,
                    }
                else:
                    detail["sync"] = {"skipped": "no cookie"}
            except Exception as exc:
                await session.rollback()
                detail["sync"] = {"error": str(exc)[:200]}
                errors.append({"source": source_id, "stage": "sync", "error": str(exc)[:200]})

            if payload.discover:
                try:
                    discover_result = await sync_service.discover_and_sync_all(
                        source_id,
                        max_pages=payload.max_discover_pages,
                    )
                    detail["discover"] = {
                        "pages_checked": discover_result["pages_checked"],
                        "books_found": discover_result["books_found"],
                        "books_synced": discover_result["books_synced"],
                        "books_failed": discover_result["books_failed"],
                        "chapters_created": discover_result["chapters_created"],
                    }
                except Exception as exc:
                    await session.rollback()
                    detail["discover"] = {"error": str(exc)[:200]}
                    errors.append({"source": source_id, "stage": "discover", "error": str(exc)[:200]})
        return detail, errors

    concurrency = min(
        max(1, int(getattr(settings, "SYNC_WORKER_CONCURRENCY", 2))),
        sync_thread_count(),
    )
    semaphore = asyncio.Semaphore(concurrency)

    async def _limited(src_info):
        async with semaphore:
            return await _sync_source(src_info)

    outcomes = await asyncio.gather(
        *(_limited(src_info) for src_info in import_result.sources),
        return_exceptions=True,
    )
    for src_info, outcome in zip(import_result.sources, outcomes):
        if isinstance(outcome, BaseException):
            result.errors.append({
                "source": src_info["id"],
                "stage": "source",
                "error": str(outcome)[:200],
            })
            result.details.append({
                "source_id": src_info["id"],
                "name": src_info.get("name", src_info["id"]),
                "sync": {"error": str(outcome)[:200]},
                "discover": {},
            })
            continue
        detail, errors = outcome
        result.details.append(detail)
        result.errors.extend(errors)
        sync_info = detail.get("sync") or {}
        discover_info = detail.get("discover") or {}
        result.books_synced += sync_info.get("books_found", 0)
        result.chapters_downloaded += sync_info.get("chapters_downloaded", 0)
        result.chapters_downloaded += discover_info.get("chapters_created", 0)
        result.books_discovered += discover_info.get("books_found", 0)

    return result


@router.post("/preview")
async def preview_yuedu_sources(
    payload: YueduImportRequest,
    user: User = Depends(get_current_user),
):
    """Preview what sources a URL or JSON text would import without saving."""
    scope = payload.scope or "personal"
    if scope == "global" and user.role not in ("admin", "super_admin"):
        raise HTTPException(status_code=403, detail="Only admins can preview global sources")
    sources_json: list[dict[str, Any]] = []

    if payload.json_text:
        try:
            sources_json = await _load_sources_from_text(payload.json_text)
        except json.JSONDecodeError as e:
            raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")
        except httpx.HTTPError as e:
            raise HTTPException(status_code=502, detail=f"Failed to fetch source URL: {e}")
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    elif payload.url:
        try:
            sources_json = await _fetch_sources_from_url(payload.url)
        except httpx.HTTPError as e:
            raise HTTPException(status_code=502, detail=f"Failed to fetch URL: {e}")
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except json.JSONDecodeError as e:
            raise HTTPException(status_code=400, detail=f"URL returned invalid JSON: {e}")

    else:
        raise HTTPException(status_code=400, detail="Either url or json_text is required")

    preview = []
    for src in sources_json:
        preview.append({
            "name": src.get("bookSourceName", "Unknown"),
            "url": src.get("bookSourceUrl", ""),
            "group": src.get("bookSourceGroup", ""),
            "is_r18": payload.is_r18,
            "type": _source_type_name(src.get("bookSourceType", 0)),
        })

    return {"count": len(preview), "sources": preview}


def _extract_sources(parsed: Any) -> list[dict[str, Any]]:
    """Extract source list from various JSON shapes."""
    if isinstance(parsed, list):
        return _dedupe_sources(parsed)
    if isinstance(parsed, dict):
        for key in ("value", "data", "sources", "bookSources"):
            value = parsed.get(key)
            if isinstance(value, list):
                return _dedupe_sources(value)
        if "bookSourceName" in parsed:
            return _dedupe_sources([parsed])
        for v in parsed.values():
            if isinstance(v, list):
                return _dedupe_sources(v)
    return []


def _make_source_id(name: str, url: str, owner_id: str | None = None) -> str:
    """Generate a unique source ID from name and URL."""
    raw = f"{name}_{url}" if not owner_id else f"{owner_id}_{name}_{url}"
    suffix = hashlib.md5(raw.encode()).hexdigest()[:12]
    return f"yuedu_{suffix}" if not owner_id else f"user:{owner_id}:yuedu_{suffix}"


def _source_type_name(t: int) -> str:
    if t == 0:
        return "text"
    elif t == 1:
        return "audio"
    elif t == 2:
        return "image"
    return "unknown"
