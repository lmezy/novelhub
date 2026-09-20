"""Sync-failure diagnosis: evidence collection, answer validation, proposals.

The tests that matter most here are the safety ones: a diagnosis must never
write to ``sources.config``, and it must not propose rule edits for failures the
model itself classified as site-side.
"""

import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models import Cookie, CrawlTask, Source, SourceCredential
from app.services.ai_client import LLMResult
from app.services.ai_config import AIConfig
from app.services.ai_diagnosis import (
    auto_diagnose_task,
    build_messages,
    build_patch,
    collect_evidence,
    cookie_names,
    describe_changes,
    diagnose_task,
    group_failures,
    normalize_change_path,
    render_config,
    render_evidence,
    render_login_state,
    sanitize_diagnosis,
    task_deserves_diagnosis,
    value_at_path,
)


def _query_entity(query):
    """Which model a ``select(...)`` targets (so the fake can route by model)."""
    try:
        return query.column_descriptions[0].get("entity")
    except Exception:  # pragma: no cover - defensive
        return None


class FakeDB:
    def __init__(self, *, task=None, source=None, scalar_value=None, scalars_values=None,
                 cookie_rows=None, credential=None):
        self.task = task
        self.source = source
        self.scalar_value = scalar_value
        self.scalars_values = list(scalars_values or [])
        self.cookie_rows = list(cookie_rows or [])
        self.credential = credential
        self.added: list = []
        self.commits = 0

    async def get(self, model, key):
        if model is CrawlTask:
            return self.task
        if model is Source:
            return self.source
        return None

    async def scalars(self, query):
        if _query_entity(query) is Cookie:
            return list(self.cookie_rows)
        return list(self.scalars_values)

    async def scalar(self, query):
        entity = _query_entity(query)
        if entity is Cookie:
            return self.cookie_rows[0] if self.cookie_rows else None
        if entity is SourceCredential:
            return self.credential
        return self.scalar_value

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def refresh(self, obj):
        return None

    async def flush(self):
        return None


def make_task(**overrides):
    values = {
        "id": "t1",
        "source": "yuedu_abc",
        "mode": "discover_all",
        "status": "failed",
        "max_pages": 0,
        "error": "同步连续失败超过 10 本，已中止任务以避免持续请求被反爬的站点",
        "exclude_tags": [],
        "exclude_categories": [],
        "result": {
            "books_found": 12,
            "books_synced": 2,
            "books_failed": 10,
            "chapters_created": 30,
            "chapters_failed": 0,
            "pages_checked": 1,
            "details": [
                {"title": "书A", "url": "https://s/1", "synced": False,
                 "error": "Site returned an anti-bot/captcha page"},
                {"title": "书B", "url": "https://s/2", "synced": False,
                 "error": "Site returned an anti-bot/captcha page"},
                {"title": "书C", "url": "https://s/3", "synced": True, "book_id": "b3"},
                {"title": "书D", "url": "https://s/4", "synced": False,
                 "error": "Upstream server returned a transient 5xx error page",
                 "failed_chapters": [{"title": "第1章", "error": "520"}]},
            ],
        },
        "progress": {"pages_checked": 1},
        "created_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def make_source(**overrides):
    values = {
        "id": "yuedu_abc",
        "name": "示例书源",
        "url": "https://example.com",
        "plugin_name": "yuedu",
        "enabled": True,
        "config": {
            "bookSourceName": "示例书源",
            "searchUrl": "/search?q={{key}}",
            "ruleToc": {"chapterList": "class.chapter-list@li@a"},
            "longValue": "x" * 5000,
        },
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def valid_answer(**overrides):
    answer = {
        "classification": "site_side",
        "confidence": "high",
        "summary": "站点返回了反爬拦截页，属于站点侧问题，与书源规则无关。",
        "reasoning": ["10 本书都返回 anti-bot/captcha 页", "同源最近任务同样失败"],
        "next_steps": ["在浏览器里通过验证后导入 Cookie", "稍后重试"],
        "proposed_changes": [],
    }
    answer.update(overrides)
    return answer


def config_answer(**overrides):
    answer = {
        "classification": "config",
        "confidence": "high",
        "summary": "目录规则命中了页脚链接。",
        "reasoning": ["失败章节 URL 都是 /cdn-cgi/l/email-protection"],
        "next_steps": ["修正 chapterList 规则"],
        "proposed_changes": [{
            "path": "ruleToc.chapterList",
            "old": "class.chapter-list@li@a",
            "new": "#chapter-list li a",
            "reason": "排除页脚邮箱保护链接",
            "risk": "low",
        }],
    }
    answer.update(overrides)
    return answer


# ---------------------------------------------------------------------------
# evidence
# ---------------------------------------------------------------------------


def test_group_failures_counts_and_samples():
    groups = group_failures(make_task().result["details"])

    assert [g["error"] for g in groups] == [
        "Site returned an anti-bot/captcha page",
        "Upstream server returned a transient 5xx error page",
    ]
    assert groups[0]["count"] == 2
    assert [e["title"] for e in groups[0]["examples"]] == ["书A", "书B"]
    assert groups[1]["failed_chapters"][0]["error"] == "520"


def test_group_failures_labels_an_empty_error_text():
    groups = group_failures([
        {"title": "书", "url": "u", "synced": False, "error": ""},
        {"title": "书2", "url": "u2", "synced": False},
    ])

    assert len(groups) == 1
    assert groups[0]["count"] == 2
    assert "错误信息为空" in groups[0]["error"]


def test_render_config_keeps_every_rule_visible():
    rendered = render_config(make_source().config, budget=1000)

    assert "--- bookSourceName ---" in rendered
    assert "--- ruleToc ---" in rendered
    assert "--- longValue ---" in rendered
    assert "已截断" in rendered


def test_render_evidence_includes_the_failure_shape():
    evidence = SimpleNamespace(
        task=make_task().__dict__,
        source={"name": "示例书源", "id": "yuedu_abc", "plugin_name": "yuedu",
                "enabled": True, "url": "https://example.com",
                "config": make_source().config},
        problem_groups=group_failures(make_task().result["details"]),
        recent_tasks=[{"created_at": "2026-09-17T02:00:00", "status": "failed",
                       "books_failed": 9, "error": "anti-bot"}],
        counts={"books_found": 12, "books_synced": 2, "books_failed": 10,
                "chapters_created": 30, "chapters_failed": 0, "pages_checked": 1},
    )

    text = render_evidence(evidence)

    assert "示例书源" in text
    assert "反爬的站点" in text
    assert "×2 Site returned an anti-bot/captcha page" in text
    assert "该源最近几次任务" in text
    assert "ruleToc" in text


def test_render_evidence_reports_the_configured_request_interval():
    """The model has to see the 拉取间隔 to diagnose a rate-limit block."""
    base = {
        "name": "搬山人", "id": "yuedu_bs", "plugin_name": "yuedu",
        "enabled": True, "url": "https://example.com",
        "config": make_source().config,
    }
    counts = {"books_found": 0, "books_synced": 0, "books_failed": 0,
              "chapters_created": 0, "chapters_failed": 0, "pages_checked": 0}

    throttled = render_evidence(SimpleNamespace(
        task=make_task().__dict__,
        source={**base, "sync_interval_seconds": 60},
        problem_groups=[], recent_tasks=[], counts=counts,
    ))
    unset = render_evidence(SimpleNamespace(
        task=make_task().__dict__,
        source={**base, "sync_interval_seconds": None},
        problem_groups=[], recent_tasks=[], counts=counts,
    ))

    assert "每 60 秒最多 1 次请求" in throttled
    assert "未配置" in unset


@pytest.mark.asyncio
async def test_collect_evidence_reads_task_source_and_history():
    db = FakeDB(task=make_task(), source=make_source(), scalars_values=[
        SimpleNamespace(id="t0", status="failed", mode="discover_all",
                        error="anti-bot", result={"books_failed": 5},
                        created_at=None),
    ])

    evidence = await collect_evidence(db, db.task)

    assert evidence.source["name"] == "示例书源"
    assert evidence.counts["books_failed"] == 10
    assert evidence.recent_tasks[0]["id"] == "t0"


def make_cookie_row(**overrides):
    values = {
        "id": "c1",
        "source": "yuedu_abc",
        "cookie_data": "ss_userid=283; cf_clearance=abc123; fontsize=16px",
        "expired_at": None,
        "created_at": datetime(2026, 9, 18, 21, 39, 29),
        "updated_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_cookie_names_lists_names_but_never_values():
    names = cookie_names("ss_userid=283; cf_clearance=abc123; fontsize=16px")

    assert names == ["ss_userid", "cf_clearance", "fontsize"]
    assert all("283" not in name and "abc123" not in name for name in names)


def test_cookie_names_ignores_a_undecryptable_blob():
    """A COOKIE_SECRET mismatch hands back raw base64; that is not a name."""
    blob = "pJ3r9F0kQ2xhbW9uZHNhZGRhc2Rhc2Rhc2Rhc2Rhc2Rhc2Rhc2Q="

    assert cookie_names(blob) == []


@pytest.mark.asyncio
async def test_collect_evidence_flags_a_cookie_it_cannot_decrypt():
    db = FakeDB(
        task=make_task(),
        source=make_source(),
        cookie_rows=[make_cookie_row(cookie_data="pJ3r9F0kQ2xhbW9uZHNhZGRh")],
    )

    state = (await collect_evidence(db, db.task)).source["cookies"]

    assert state["configured"] is True
    assert state["decrypted"] is False
    assert state["names"] == []
    assert "已保存 Cookie" in "\n".join(render_login_state({"cookies": state}))


@pytest.mark.asyncio
async def test_collect_evidence_reports_the_stored_cookie():
    """The model used to be told nothing about a Cookie that does exist.

    ``sync_diagnoses`` on the live instance holds an answer that says
    「书源未配置 Cookie，header 中只有 UA」 for 中文成人文学网-短篇(简体) -- a source
    whose Cookie had been saved one minute earlier.  The rules JSON never
    mentions cookies, so the evidence has to carry the login state itself.
    """
    db = FakeDB(
        task=make_task(),
        source=make_source(),
        cookie_rows=[make_cookie_row(source="yuedu_abc")],
    )

    evidence = await collect_evidence(db, db.task)

    state = evidence.source["cookies"]
    assert state["configured"] is True
    assert state["count"] == 1
    assert state["names"] == ["ss_userid", "cf_clearance", "fontsize"]
    assert state["created_at"] == "2026-09-18T21:39:29"
    assert state["credentials_saved"] is False
    # The prompt must never carry the cookie's value.
    assert "283" not in json.dumps(evidence.as_dict(), ensure_ascii=False)
    assert "abc123" not in json.dumps(evidence.as_dict(), ensure_ascii=False)


@pytest.mark.asyncio
async def test_collect_evidence_marks_an_expired_cookie_and_saved_credentials():
    db = FakeDB(
        task=make_task(),
        source=make_source(),
        cookie_rows=[make_cookie_row(expired_at=datetime(2020, 1, 1))],
        credential=SimpleNamespace(username="u"),
    )

    state = (await collect_evidence(db, db.task)).source["cookies"]

    assert state["expired"] is True
    assert state["credentials_saved"] is True


@pytest.mark.asyncio
async def test_collect_evidence_reads_the_most_recently_written_cookie():
    """The row written last describes the live login, whatever it was created.

    Updating a Cookie replaces ``cookie_data`` in place, so ordering by
    ``created_at`` alone can hand the model a value that was replaced since.
    """
    db = FakeDB(
        task=make_task(),
        source=make_source(),
        cookie_rows=[
            make_cookie_row(id="c-old", created_at=datetime(2026, 9, 18, 9, 0),
                            updated_at=datetime(2026, 9, 18, 9, 0),
                            cookie_data="fontsize=16px"),
            make_cookie_row(id="c-new", created_at=datetime(2026, 8, 11, 20, 52, 52),
                            updated_at=datetime(2026, 9, 20, 12, 21),
                            cookie_data="cf_clearance=fresh; fontsize=16px"),
        ],
    )

    state = (await collect_evidence(db, db.task)).source["cookies"]

    assert state["names"] == ["cf_clearance", "fontsize"]
    assert state["created_at"] == "2026-08-11T20:52:52"
    assert state["updated_at"] == "2026-09-20T12:21:00"


@pytest.mark.asyncio
async def test_collect_evidence_without_a_cookie_says_so():
    db = FakeDB(task=make_task(), source=make_source())

    state = (await collect_evidence(db, db.task)).source["cookies"]

    assert state["configured"] is False
    assert state["count"] == 0
    assert state["names"] == []


def test_render_evidence_tells_the_model_a_cookie_is_configured():
    evidence = SimpleNamespace(
        task=make_task().__dict__,
        source={
            "name": "中文成人文学网", "id": "yuedu_c4a", "plugin_name": "yuedu",
            "enabled": True, "url": "https://blog.xbookcn.net",
            "config": make_source().config,
            "cookies": {"configured": True, "count": 1,
                        "names": ["ss_userid", "cf_clearance"],
                        "created_at": "2026-09-18T21:39:29", "expired_at": None,
                        "expired": False, "credentials_saved": False},
        },
        problem_groups=[], recent_tasks=[],
        counts={"books_found": 0, "books_synced": 0, "books_failed": 0,
                "chapters_created": 0, "chapters_failed": 0, "pages_checked": 0},
    )

    text = render_evidence(evidence)

    assert "## 登录状态（Cookie）" in text
    assert "已保存 Cookie" in text
    assert "cf_clearance" in text
    assert "没有保存 Cookie" not in text
    # The prompt has to say out loud that the rules JSON is not the source of
    # truth for cookies, otherwise the model repeats the wrong conclusion.
    assert "不代表" in text


def test_render_login_state_separates_first_save_from_the_last_write():
    """Live ``sync_diagnoses`` blamed a Cookie that had been refreshed that day.

    The prompt carried only ``created_at``, so the model computed「间隔约 40 天」
    from the row's first import and told the user to go export a fresh Cookie.
    """
    lines = render_login_state({"cookies": {
        "configured": True, "count": 1, "names": ["cf_clearance"],
        "created_at": "2026-08-11T20:52:52",
        "updated_at": "2026-09-20T12:21:58",
        "expired_at": None, "expired": False, "credentials_saved": False,
    }})

    text = "\n".join(lines)
    assert "最近写入：2026-09-20T12:21:58" in text
    assert "首次保存：2026-08-11T20:52:52" in text
    assert "以「最近写入」为准" in text


def test_render_login_state_falls_back_to_created_at_without_a_write_time():
    lines = render_login_state({"cookies": {
        "configured": True, "count": 1, "names": ["cf_clearance"],
        "created_at": "2026-08-11T20:52:52", "expired_at": None,
        "expired": False, "credentials_saved": False,
    }})

    assert "最近写入：2026-08-11T20:52:52" in "\n".join(lines)


def test_render_login_state_says_no_cookie_only_when_the_store_is_empty():
    lines = render_login_state({
        "cookies": {"configured": False, "count": 0, "names": [],
                    "credentials_saved": False},
    })

    assert "没有保存 Cookie" in lines[0]
    assert "没有保存自动登录凭据" in lines[1]


def test_render_login_state_without_evidence_is_not_a_claim():
    assert render_login_state({}) == ["- Cookie 状态：证据未提供"]


def test_system_prompt_forbids_cookie_claims_from_the_rule_json():
    messages = build_messages(SimpleNamespace(
        task=make_task().__dict__,
        source={"name": "s", "id": "i", "plugin_name": "yuedu", "enabled": True,
                "url": "u", "config": make_source().config},
        problem_groups=[], recent_tasks=[],
        counts={"books_found": 0, "books_synced": 0, "books_failed": 0,
                "chapters_created": 0, "chapters_failed": 0, "pages_checked": 0},
    ))

    prompt = messages[0]["content"]

    assert "登录状态" in prompt
    assert "**不要**因为规则里看不到" in prompt


def test_build_messages_embeds_the_rules():
    evidence = SimpleNamespace(
        task=make_task().__dict__,
        source={"name": "s", "id": "i", "plugin_name": "yuedu", "enabled": True,
                "url": "u", "config": make_source().config},
        problem_groups=[], recent_tasks=[],
        counts={"books_found": 0, "books_synced": 0, "books_failed": 0,
                "chapters_created": 0, "chapters_failed": 0, "pages_checked": 0},
    )

    messages = build_messages(evidence)

    assert messages[0]["role"] == "system"
    assert "红线" in messages[0]["content"]
    assert "searchUrl" in messages[1]["content"]


# ---------------------------------------------------------------------------
# answer validation (the safety net)
# ---------------------------------------------------------------------------


def test_site_side_failure_can_never_propose_rule_changes():
    """A Cloudflare/5xx failure must not come back with a book-source edit."""
    diagnosis = sanitize_diagnosis(
        valid_answer(proposed_changes=[{
            "path": "config.ruleToc.chapterList", "new": "li a",
            "reason": "irrelevant", "risk": "low",
        }])
    )

    assert diagnosis["classification"] == "site_side"
    assert diagnosis["proposed_changes"] == []
    assert diagnosis["dropped_changes"] == 1


def test_config_failure_keeps_sane_changes():
    diagnosis = sanitize_diagnosis(config_answer())

    assert diagnosis["classification"] == "config"
    change = diagnosis["proposed_changes"][0]
    assert change["path"] == "config.ruleToc.chapterList"
    assert change["risk"] == "low"


def test_sanitize_rejects_malformed_changes():
    diagnosis = sanitize_diagnosis(config_answer(proposed_changes=[
        {"path": "", "new": "x"},
        {"path": "ruleToc.chapterList", "new": ""},
        {"path": "ruleToc.chapterList", "new": "ok", "risk": "weird"},
        "not-an-object",
    ]))

    assert len(diagnosis["proposed_changes"]) == 1
    assert diagnosis["proposed_changes"][0]["risk"] == "medium"
    assert diagnosis["dropped_changes"] == 3


def test_sanitize_normalises_unknown_classification():
    diagnosis = sanitize_diagnosis({"classification": "aliens", "summary": "?"})

    assert diagnosis["classification"] == "unknown"
    assert diagnosis["confidence"] == "medium"
    assert diagnosis["summary"] == "?"


def test_sanitize_caps_lists_and_values():
    diagnosis = sanitize_diagnosis(config_answer(
        reasoning=[f"r{i}" for i in range(30)],
        proposed_changes=[
            {"path": f"config.k{i}", "new": "v"} for i in range(30)
        ],
    ))

    assert len(diagnosis["reasoning"]) == 8
    assert len(diagnosis["proposed_changes"]) == 8


def test_change_path_normalisation():
    assert normalize_change_path("ruleToc.chapterList") == "config.ruleToc.chapterList"
    assert normalize_change_path("config.ruleToc") == "config.ruleToc"
    assert normalize_change_path("enabled") == "enabled"
    assert normalize_change_path("sync_interval_seconds") == "sync_interval_seconds"
    assert normalize_change_path("") == ""


def test_describe_changes_reads_a_source_column_not_only_config():
    """A proposal that raises the 拉取间隔 must show the really stored value."""
    source_info = {
        "config": {"concurrentRate": "1000"},
        "enabled": True,
        "sync_interval_seconds": 60,
    }

    described = describe_changes(source_info, [{
        "path": "sync_interval_seconds", "old": "60", "new": 120,
        "reason": "站点要求每分钟一次", "risk": "low",
    }])
    stale = describe_changes(source_info, [{
        "path": "sync_interval_seconds", "old": "5", "new": 120,
    }])

    assert described[0]["current"] == "60"
    assert described[0]["mismatch"] is False
    assert described[0]["missing"] is False
    assert stale[0]["mismatch"] is True


def test_describe_changes_flags_a_stale_old_value():
    config = {"ruleToc": {"chapterList": "current-value"}}

    described = describe_changes(config, [{
        "path": "config.ruleToc.chapterList", "old": "what-the-model-thinks",
        "new": "new-value", "reason": "why", "risk": "low",
    }])
    matching = describe_changes(config, [{
        "path": "config.ruleToc.chapterList", "old": "current-value",
        "new": "new-value",
    }])
    truncated = describe_changes(config, [{
        "path": "config.ruleToc.chapterList", "old": "curren…（已截断）",
        "new": "new-value",
    }])

    assert described[0]["mismatch"] is True
    assert described[0]["current"] == "current-value"
    assert matching[0]["mismatch"] is False
    assert truncated[0]["mismatch"] is False


def test_value_at_path_and_patch_building():
    config = {"a": {"b": {"c": 1}}}

    assert value_at_path(config, "config.a.b.c") == 1
    assert value_at_path(config, "a.b.c") == 1
    assert value_at_path(config, "config.a.zzz") is None

    patch = build_patch([
        {"path": "config.ruleToc.chapterList", "new": "#list li a"},
        {"path": "enabled", "new": False},
        {"path": "config.concurrentRate", "new": "2000"},
        {"path": "sync_interval_seconds", "new": 60},
    ])
    assert patch == {
        "config": {"ruleToc": {"chapterList": "#list li a"},
                   "concurrentRate": "2000"},
        "enabled": False,
        "sync_interval_seconds": 60,
    }

    # A bare rule dict (no surrounding source info) still resolves config paths.
    assert value_at_path(config, "sync_interval_seconds") is None


# ---------------------------------------------------------------------------
# running a diagnosis
# ---------------------------------------------------------------------------


def configured() -> AIConfig:
    return AIConfig(enabled=True, provider="openai", api_key="sk-test",
                    model="gpt-4o-mini")


def fake_llm(answer: dict, text: str | None = None):
    client = MagicMock()
    client.chat = AsyncMock(return_value=LLMResult(
        text if text is not None else json.dumps(answer, ensure_ascii=False),
        model="fake-model", prompt_tokens=100, completion_tokens=50,
    ))
    return client


@pytest.mark.asyncio
async def test_diagnose_task_stores_the_answer_and_never_touches_the_source():
    task = make_task()
    source = make_source()
    original_config = json.loads(json.dumps(source.config))
    db = FakeDB(task=task, source=source)

    with patch("app.services.ai_diagnosis.LLMClient",
               return_value=fake_llm(config_answer())):
        result = await diagnose_task(db, "t1", config=configured())

    assert result["classification"] == "config"
    assert result["tokens_used"] == 150
    assert result["proposed_changes"][0]["path"] == "config.ruleToc.chapterList"
    assert db.added and db.added[0].task_id == "t1"
    # The whole point: the source rules are untouched.
    assert source.config == original_config


@pytest.mark.asyncio
async def test_diagnose_task_rejects_a_non_json_answer():
    from app.services.ai_client import AIError

    db = FakeDB(task=make_task(), source=make_source())

    with patch("app.services.ai_diagnosis.LLMClient",
               return_value=fake_llm({}, text="我觉得是站点问题")):
        with pytest.raises(AIError) as excinfo:
            await diagnose_task(db, "t1", config=configured())

    assert "JSON" in str(excinfo.value)
    assert db.added == []


@pytest.mark.asyncio
async def test_diagnose_task_requires_a_configured_provider():
    from app.services.ai_client import AIError
    from app.services.ai_config import AIConfig

    db = FakeDB(task=make_task(), source=make_source())

    with pytest.raises(AIError) as excinfo:
        await diagnose_task(db, "t1", config=AIConfig(enabled=False))

    assert "设置" in str(excinfo.value)


@pytest.mark.asyncio
async def test_diagnose_task_returns_the_stored_one_unless_forced():
    stored = SimpleNamespace(
        id="d1", task_id="t1", source_id="yuedu_abc", status="ok",
        classification="site_side", confidence="high", summary="已诊断",
        payload={"proposed_changes": []}, error=None, model="m",
        tokens_used=1, change_id=None, created_at=None,
    )
    db = FakeDB(task=make_task(), source=make_source(), scalar_value=stored)

    with patch("app.services.ai_diagnosis.LLMClient") as client:
        result = await diagnose_task(db, "t1", config=configured())

    assert result["id"] == "d1"
    client.assert_not_called()


@pytest.mark.asyncio
async def test_diagnose_task_raises_for_a_missing_task():
    db = FakeDB(task=None)

    with pytest.raises(ValueError):
        await diagnose_task(db, "nope")


# ---------------------------------------------------------------------------
# automatic run
# ---------------------------------------------------------------------------


def test_task_deserves_diagnosis_only_for_error_states():
    assert task_deserves_diagnosis(make_task(status="failed")) is True
    assert task_deserves_diagnosis(make_task(status="completed")) is False
    assert task_deserves_diagnosis(make_task(status="cancelled")) is False
    assert task_deserves_diagnosis(make_task(status="running")) is False
    assert task_deserves_diagnosis(make_task(
        status="completed_with_errors", result={"books_failed": 3})) is True
    assert task_deserves_diagnosis(make_task(
        status="completed_with_errors", result={"books_failed": 0,
                                                "chapters_failed": 0})) is False


@pytest.mark.asyncio
async def test_auto_diagnose_is_skipped_when_the_feature_is_off():
    from app.services.ai_config import AIConfig

    db = FakeDB(task=make_task(), source=make_source())
    session_factory = MagicMock()
    session_factory.return_value.__aenter__ = AsyncMock(return_value=db)
    session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    with patch("app.services.ai_diagnosis.get_ai_config",
               AsyncMock(return_value=AIConfig(enabled=True, provider="openai",
                                               api_key="k", auto_diagnose=False))):
        with patch("app.services.ai_diagnosis.diagnose_task") as run:
            result = await auto_diagnose_task("t1", session_factory=session_factory)

    assert result is None
    run.assert_not_called()


@pytest.mark.asyncio
async def test_auto_diagnose_is_skipped_for_a_successful_task():
    db = FakeDB(task=make_task(status="completed"), source=make_source())
    session_factory = MagicMock()
    session_factory.return_value.__aenter__ = AsyncMock(return_value=db)
    session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    with patch("app.services.ai_diagnosis.diagnose_task") as run:
        result = await auto_diagnose_task("t1", session_factory=session_factory)

    assert result is None
    run.assert_not_called()


@pytest.mark.asyncio
async def test_auto_diagnose_never_raises_and_reports_ai_errors():
    from app.services.ai_client import AIError
    from app.services.ai_config import AIConfig

    db = FakeDB(task=make_task(), source=make_source())
    session_factory = MagicMock()
    session_factory.return_value.__aenter__ = AsyncMock(return_value=db)
    session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    with patch("app.services.ai_diagnosis.get_ai_config",
               AsyncMock(return_value=AIConfig(enabled=True, provider="openai",
                                               api_key="k"))):
        with patch("app.services.ai_diagnosis.diagnose_task",
                   AsyncMock(side_effect=AIError("连不上"))):
            assert await auto_diagnose_task("t1", session_factory=session_factory) is None

        with patch("app.services.ai_diagnosis.diagnose_task",
                   AsyncMock(side_effect=RuntimeError("boom"))):
            assert await auto_diagnose_task("t1", session_factory=session_factory) is None


@pytest.mark.asyncio
async def test_auto_diagnose_runs_once_when_enabled():
    from app.services.ai_config import AIConfig

    db = FakeDB(task=make_task(), source=make_source())
    session_factory = MagicMock()
    session_factory.return_value.__aenter__ = AsyncMock(return_value=db)
    session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
    expected = {"task_id": "t1", "classification": "site_side"}

    with patch("app.services.ai_diagnosis.get_ai_config",
               AsyncMock(return_value=AIConfig(enabled=True, provider="openai",
                                               api_key="k"))):
        with patch("app.services.ai_diagnosis.diagnose_task",
                   AsyncMock(return_value=expected)) as run:
            assert await auto_diagnose_task(
                "t1", session_factory=session_factory) == expected

    assert run.await_args.kwargs["force"] is True
