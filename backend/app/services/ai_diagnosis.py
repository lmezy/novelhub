"""Explain a failed sync task, and propose -- never apply -- a fix.

The AI is deliberately a **read-only adviser** here:

* it gets the task's error(s), the failing books/chapters and the book source's
  own rule JSON;
* it answers a fixed JSON schema: what kind of problem this is, why, what to do
  next, and (only for configuration problems) a list of field-level changes;
* the changes are turned into a normal ``source_changes`` proposal that an
  admin approves or rejects in the existing 审批 UI.

Nothing in this module writes ``sources.config``.  That is the point: most sync
failures are site-side (Cloudflare, 5xx, proxy jitter, rate limiting, deleted
books) and "fixing" the rules for those would silently corrupt a working source.
The prompt says so explicitly, and ``sanitize_diagnosis`` drops proposed rule
changes unless the model classified the failure as a configuration problem.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable
from uuid import uuid4

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import SessionLocal
from app.models import CrawlTask, Source, SyncDiagnosis
from app.services.ai_client import AIError, LLMClient
from app.services.ai_config import AIConfig, get_ai_config, not_configured_reason
from app.services.source_interval import source_sync_interval

#: Failure classes the model must choose from.
CLASSIFICATIONS = (
    "site_side",      # 反爬/验证码/站点故障，配置无关
    "cookie",         # Cookie 缺失或过期
    "rate_limit",     # 站点限速/请求过快
    "proxy",          # 代理或网络抖动
    "config",         # 书源规则/字段配置问题
    "removed_books",  # 源站已删除的书
    "unknown",
)

#: Classes for which proposing rule changes is allowed at all.
CONFIG_LIKE = ("config", "rate_limit", "cookie")

#: ``SourceChange`` actions produced by a diagnosis.
PROPOSAL_ORIGIN = "ai"

#: Book source columns a proposal may touch (never arbitrary attributes).
SOURCE_FIELDS = (
    "enabled",
    "url",
    "name",
    "plugin_name",
    "is_r18",
    "sync_interval_seconds",
)

MAX_PROBLEM_GROUPS = 12
MAX_EXAMPLES_PER_GROUP = 4
MAX_PROPOSED_CHANGES = 8
MAX_CHANGE_VALUE_CHARS = 4000
MAX_SUMMARY_CHARS = 1200
MAX_LIST_ITEMS = 8
MAX_ITEM_CHARS = 600
DEFAULT_CONFIG_BUDGET = 40000
MIN_PER_RULE_CHARS = 400

#: Hard rules handed to the model.  The last two are the red lines from
#: ``docs/NovelHub-AI-Development-Context.md``.
SYSTEM_DIAGNOSE = """你是 NovelHub（自托管小说采集平台）的排错助手。用户会给你一次同步任务的报错、
失败书籍/章节样本、以及该**书源（Legado/YueDu 规则 JSON）**的当前配置。你的任务是判断这次失败
的真正原因，并给出可执行的下一步。

必须先分清两类问题：
A. **站点侧 / 环境侧**（占绝大多数）：Cloudflare 人机验证、5xx、502/520、代理抖动、
   站点限速、账号或 Cookie 失效、书被源站删除、站点改版导致规则整体失效。
   —— 这类问题**不要**提议改书源规则，改配置解决不了。
B. **书源配置问题**：某条规则写法与本站页面结构不符（选择器写法不被支持、规则命中了导航/
   页脚、字段缺失、UA 与站点要求的形态不一致等）。只有 B 类才允许提议修改书源配置。

已知的项目特有现象（用于对照，不要机械套用）：
- 页面正常却报“验证码/人机验证”：正文里出现「已被限制」「身份验证」这类词曾被误判，代码已修；
  若仍出现，多为站点真的限速或 Cookie 过期。
- 目录/分类页解析出 0 本：常见于代理抖动（HTTP 200 但内容为空）或规则选择器不被支持。
- 章节正文为空：可能是源站已删书、Cloudflare 520、或规则失效（图片型章节需图片兜底）。
- Cookie 类站点报拦截：需要在浏览器里过验证后重新导入 Cookie，**不能**靠改规则解决。
- 连续多章/多本被拦（验证码、403、520）且书源自带的 concurrentRate 很小：这是**站点限速**，
  正确的处置是提高 sync_interval_seconds（每个请求之间的秒数），而不是改解析规则。

**红线（违反即视为无效回答）**：
1. 不得建议绕过验证码 / WAF / 登录限制，也不得建议伪造身份或抓取受限内容；
2. 不得为单个站点写死逻辑；
3. 只有在分类为 config / rate_limit / cookie，且你确信是配置写法问题时，才填写 proposed_changes；
   其他分类必须返回空数组。

只输出一个 JSON 对象，不要输出任何解释文字或 Markdown 围栏，格式：
{
  "classification": "site_side|cookie|rate_limit|proxy|config|removed_books|unknown",
  "confidence": "high|medium|low",
  "summary": "一到两句话的结论：这次失败是什么原因",
  "reasoning": ["从证据到结论的推理，每条一句话"],
  "next_steps": ["具体动作，写清谁做什么，例如：稍后重试 / 去浏览器导出 Cookie 再导入 / 检查代理"],
  "proposed_changes": [
    {
      "path": "config.<顶层键>.<子键>…（只允许 config. 开头的规则路径，或 enabled/url/name/is_r18/sync_interval_seconds 这几个源字段）",
      "old": "当前值（照抄你看到的原文，不确定就留空字符串）",
      "new": "建议改成的新值",
      "reason": "为什么这样改能解决本次报错",
      "risk": "low|medium|high"
    }
  ]
}"""


def _trim(text: Any, limit: int) -> str:
    value = str(text or "")
    return value if len(value) <= limit else value[:limit] + "…（已截断）"


@dataclass
class Evidence:
    """Everything the model is allowed to see about one failed task."""

    task: dict[str, Any] = field(default_factory=dict)
    source: dict[str, Any] = field(default_factory=dict)
    problem_groups: list[dict[str, Any]] = field(default_factory=list)
    recent_tasks: list[dict[str, Any]] = field(default_factory=list)
    counts: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "source": self.source,
            "problem_groups": self.problem_groups,
            "recent_tasks": self.recent_tasks,
            "counts": self.counts,
        }


# ---------------------------------------------------------------------------
# evidence collection
# ---------------------------------------------------------------------------


def group_failures(details: Iterable[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Collapse per-book failure details into ``error -> count + examples``.

    A full-site task can fail 40 books with the same message; the model needs
    the *shape* of the failure (and a couple of samples), not 40 copies.
    """
    groups: dict[str, dict[str, Any]] = {}
    for entry in details or []:
        if not isinstance(entry, dict) or entry.get("synced"):
            continue
        error = str(entry.get("error") or "").strip() or "(错误信息为空：通常是超时或连接中断)"
        bucket = groups.setdefault(error, {
            "error": error,
            "count": 0,
            "examples": [],
            "failed_chapters": [],
        })
        bucket["count"] += 1
        if len(bucket["examples"]) < MAX_EXAMPLES_PER_GROUP:
            bucket["examples"].append({
                "title": _trim(entry.get("title"), 120),
                "url": _trim(entry.get("url"), 300),
            })
        for chapter in (entry.get("failed_chapters") or [])[:2]:
            if isinstance(chapter, dict) and len(bucket["failed_chapters"]) < 2:
                bucket["failed_chapters"].append({
                    "title": _trim(chapter.get("title"), 120),
                    "error": _trim(chapter.get("error"), 240),
                })

    ordered = sorted(groups.values(), key=lambda g: g["count"], reverse=True)
    return ordered[:MAX_PROBLEM_GROUPS]


async def collect_evidence(db: AsyncSession, task: CrawlTask) -> Evidence:
    """Load the task, its source rules and the recent history of that source."""
    result = task.result if isinstance(task.result, dict) else {}
    progress = task.progress if isinstance(task.progress, dict) else {}

    source = await db.get(Source, task.source) if task.source else None
    source_info: dict[str, Any] = {
        "id": task.source,
        "name": getattr(source, "name", "") or "",
        "url": getattr(source, "url", "") or "",
        "plugin_name": getattr(source, "plugin_name", "") or "",
        "enabled": bool(getattr(source, "enabled", True)),
        "sync_interval_seconds": source_sync_interval(source),
        "config": dict(getattr(source, "config", None) or {}),
    }

    recent: list[dict[str, Any]] = []
    if task.source:
        rows = await db.scalars(
            select(CrawlTask)
            .where(CrawlTask.source == task.source, CrawlTask.id != task.id)
            .order_by(CrawlTask.created_at.desc())
            .limit(5)
        )
        for row in rows:
            recent.append({
                "id": row.id,
                "status": row.status,
                "mode": row.mode,
                "error": _trim(row.error, 300),
                "books_failed": int((row.result or {}).get("books_failed", 0) or 0),
                "created_at": row.created_at.isoformat() if row.created_at else None,
            })

    counts = {
        "books_found": int(result.get("books_found", 0) or 0),
        "books_synced": int(result.get("books_synced", 0) or 0),
        "books_failed": int(result.get("books_failed", 0) or 0),
        "chapters_created": int(result.get("chapters_created", 0) or 0),
        "chapters_failed": int(result.get("chapters_failed", 0) or 0),
        "pages_checked": int(result.get("pages_checked", progress.get("pages_checked", 0)) or 0),
    }

    return Evidence(
        task={
            "id": task.id,
            "mode": task.mode,
            "status": task.status,
            "max_pages": task.max_pages,
            "error": _trim(task.error, 2000),
            "exclude_tags": list(task.exclude_tags or []),
            "exclude_categories": list(task.exclude_categories or []),
        },
        source=source_info,
        problem_groups=group_failures(result.get("details")),
        recent_tasks=recent,
        counts=counts,
    )


def render_config(config: dict[str, Any] | None, budget: int = DEFAULT_CONFIG_BUDGET) -> str:
    """Render the book source rules, one block per key, truncating long values.

    Every top-level key stays visible (the model has to know which rules exist)
    while a single huge rule cannot eat the whole context window.
    """
    config = config or {}
    if not config:
        return "(该书源没有保存任何规则配置)"

    per_key = max(MIN_PER_RULE_CHARS, budget // max(1, len(config)))
    blocks: list[str] = []
    for key, value in config.items():
        if isinstance(value, str):
            text = value
        else:
            try:
                text = json.dumps(value, ensure_ascii=False)
            except (TypeError, ValueError):
                text = str(value)
        blocks.append(f"--- {key} ---\n{_trim(text, per_key)}")
    return "\n\n".join(blocks)


def _render_interval(seconds: Any) -> str:
    """Describe a source's configured request interval for the model."""
    if seconds is None:
        return "未配置（按书源自带 concurrentRate，缺省每请求 1.2 秒）"
    try:
        value = int(seconds)
    except (TypeError, ValueError):
        return "未配置"
    if value <= 0:
        return "已显式关闭限速（0）"
    return f"每 {value} 秒最多 1 次请求（{value} 秒/请求）"


def render_evidence(evidence: Evidence, config_budget: int = DEFAULT_CONFIG_BUDGET) -> str:
    task = evidence.task
    counts = evidence.counts
    lines = [
        "## 任务",
        f"书源：{evidence.source.get('name')}（{evidence.source.get('id')}）"
        f" 插件 {evidence.source.get('plugin_name')} 启用={evidence.source.get('enabled')}",
        f"站点：{evidence.source.get('url')}",
        f"请求间隔（sync_interval_seconds）：{_render_interval(evidence.source.get('sync_interval_seconds'))}",
        f"模式：{task.get('mode')} 状态：{task.get('status')} 页数上限：{task.get('max_pages')}",
        f"计数：找到 {counts['books_found']}，成功 {counts['books_synced']}，"
        f"失败 {counts['books_failed']}，新增章节 {counts['chapters_created']}，"
        f"章节失败 {counts['chapters_failed']}，已翻页 {counts['pages_checked']}",
        f"任务级报错：{task.get('error') or '(无)'}",
    ]

    lines.append("\n## 失败内容分组（按出现次数排序）")
    if evidence.problem_groups:
        for group in evidence.problem_groups:
            lines.append(f"- ×{group['count']} {group['error']}")
            for example in group["examples"]:
                lines.append(f"    例：{example['title']} {example['url']}")
            for chapter in group["failed_chapters"]:
                lines.append(f"    失败章节：{chapter['title']} — {chapter['error']}")
    else:
        lines.append("(没有逐书失败明细)")

    lines.append("\n## 该源最近几次任务（判断是不是长期问题）")
    if evidence.recent_tasks:
        for row in evidence.recent_tasks:
            lines.append(
                f"- {row['created_at']} {row['status']} 失败 {row['books_failed']} 本："
                f"{row['error'] or '(无)'}"
            )
    else:
        lines.append("(这是该书源的第一次任务)")

    lines.append("\n## 书源规则（当前配置）")
    lines.append(render_config(evidence.source.get("config"), config_budget))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# answer parsing
# ---------------------------------------------------------------------------


def _extract_json_object(text: str) -> dict[str, Any] | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    fence = re.search(r"```(?:json)?\s*(.+?)```", raw, re.S | re.I)
    if fence:
        raw = fence.group(1).strip()
    candidates = [raw]
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end > start:
        candidates.append(raw[start:end + 1])
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(data, dict):
            return data
    return None


def _string_list(value: Any, *, limit: int = MAX_LIST_ITEMS) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value[:limit]:
        text = _trim(item, MAX_ITEM_CHARS).strip()
        if text:
            out.append(text)
    return out


def normalize_change_path(path: str) -> str:
    """Normalise ``config.ruleToc.chapterList`` / ``ruleToc.chapterList``."""
    text = str(path or "").strip().strip(".")
    if not text:
        return ""
    root = text.split(".", 1)[0]
    if root in SOURCE_FIELDS or root == "config":
        return text
    # A bare rule path such as ``ruleToc.chapterList`` lives inside config.
    return "config." + text


def sanitize_diagnosis(data: dict[str, Any] | None) -> dict[str, Any]:
    """Validate the model answer and drop anything unsafe or malformed.

    Rules enforced here (in addition to the prompt):

    * the classification must be one of :data:`CLASSIFICATIONS`;
    * ``proposed_changes`` are only kept for configuration-like classes, so a
      Cloudflare/5xx failure can never produce a rule edit;
    * every change needs a non-empty ``path`` and a scalar-ish ``new`` value;
    * values are length-capped.
    """
    data = data or {}
    classification = str(data.get("classification") or "").strip().lower()
    if classification not in CLASSIFICATIONS:
        classification = "unknown"

    confidence = str(data.get("confidence") or "").strip().lower()
    if confidence not in ("high", "medium", "low"):
        confidence = "medium"

    changes: list[dict[str, Any]] = []
    dropped = 0
    raw_changes = data.get("proposed_changes")
    if classification in CONFIG_LIKE and isinstance(raw_changes, list):
        for item in raw_changes[:MAX_PROPOSED_CHANGES]:
            if not isinstance(item, dict):
                dropped += 1
                continue
            path = normalize_change_path(item.get("path"))
            new_value = item.get("new")
            if not path or new_value is None or new_value == "":
                dropped += 1
                continue
            if not isinstance(new_value, (str, int, float, bool, list, dict)):
                dropped += 1
                continue
            try:
                rendered = (new_value if isinstance(new_value, str)
                            else json.dumps(new_value, ensure_ascii=False))
            except (TypeError, ValueError):
                dropped += 1
                continue
            if len(rendered) > MAX_CHANGE_VALUE_CHARS:
                dropped += 1
                continue
            risk = str(item.get("risk") or "").strip().lower()
            changes.append({
                "path": path,
                "old": _trim(item.get("old"), MAX_CHANGE_VALUE_CHARS),
                "new": new_value,
                "reason": _trim(item.get("reason"), MAX_ITEM_CHARS),
                "risk": risk if risk in ("low", "medium", "high") else "medium",
            })
    elif isinstance(raw_changes, list):
        dropped = len(raw_changes)

    return {
        "classification": classification,
        "confidence": confidence,
        "summary": _trim(data.get("summary"), MAX_SUMMARY_CHARS).strip(),
        "reasoning": _string_list(data.get("reasoning")),
        "next_steps": _string_list(data.get("next_steps")),
        "proposed_changes": changes,
        "dropped_changes": dropped,
    }


def build_messages(evidence: Evidence, config_budget: int = DEFAULT_CONFIG_BUDGET) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": SYSTEM_DIAGNOSE},
        {"role": "user", "content": render_evidence(evidence, config_budget)},
    ]


# ---------------------------------------------------------------------------
# proposals
# ---------------------------------------------------------------------------


def value_at_path(source: dict[str, Any], path: str) -> Any:
    """Current value of a normalised path (``config.a.b`` or a source field).

    ``source`` is either the book-source *info* dict
    (``{"config": {...}, "enabled": True, …}``) or a bare rule dict.  A
    source-info dict also resolves the patchable source columns, so a proposal
    targeting ``sync_interval_seconds`` shows the value that is really stored
    instead of an empty "missing" cell.
    """
    segments = [segment for segment in str(path or "").split(".") if segment]
    if not segments:
        return None
    root = segments[0]
    if (
        root in SOURCE_FIELDS
        and isinstance(source, dict)
        and isinstance(source.get("config"), dict)
    ):
        return source.get(root)
    config = source if isinstance(source, dict) else {}
    if root == "config":
        segments = segments[1:]
    current: Any = config or {}
    for segment in segments:
        if isinstance(current, dict) and segment in current:
            current = current[segment]
        else:
            return None
    return current


def describe_changes(source: dict[str, Any] | None,
                     changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach the *current* value to each proposed change.

    The model's ``old`` is only its recollection; showing the real current value
    next to it (and flagging a mismatch) is what makes the approval diff
    trustworthy.
    """
    source = source or {}
    described: list[dict[str, Any]] = []
    for change in changes:
        path = change.get("path") or ""
        current = value_at_path(source, path)
        if current is None:
            current_text = ""
        elif isinstance(current, str):
            current_text = current
        else:
            try:
                current_text = json.dumps(current, ensure_ascii=False)
            except (TypeError, ValueError):
                current_text = str(current)
        claimed = str(change.get("old") or "").strip()
        new_value = change.get("new")
        new_text = new_value if isinstance(new_value, str) else json.dumps(
            new_value, ensure_ascii=False)
        # A value the prompt itself truncated cannot be compared verbatim.
        truncated = "（已截断）" in claimed
        described.append({
            "path": path,
            "old": claimed,
            "current": _trim(current_text, MAX_CHANGE_VALUE_CHARS),
            "new": new_value,
            "new_text": _trim(new_text, MAX_CHANGE_VALUE_CHARS),
            "reason": change.get("reason") or "",
            "risk": change.get("risk") or "medium",
            "mismatch": bool(claimed) and not truncated and claimed != current_text,
            "missing": current is None,
        })
    return described


def build_patch(changes: list[dict[str, Any]]) -> dict[str, Any]:
    """Turn flat ``config.a.b`` paths into one nested patch object."""
    patch: dict[str, Any] = {}
    for change in changes:
        path = normalize_change_path(change.get("path"))
        if not path or change.get("new") is None:
            continue
        segments = path.split(".")
        cursor = patch
        for segment in segments[:-1]:
            nxt = cursor.get(segment)
            if not isinstance(nxt, dict):
                nxt = {}
                cursor[segment] = nxt
            cursor = nxt
        cursor[segments[-1]] = change["new"]
    return patch


# ---------------------------------------------------------------------------
# running a diagnosis
# ---------------------------------------------------------------------------


async def diagnose_task(db: AsyncSession, task_id: str, *,
                        force: bool = False,
                        store: bool = True,
                        config: AIConfig | None = None) -> dict[str, Any]:
    """Analyse a task; returns the stored diagnosis as a dict.

    Raises :class:`ValueError` when the task does not exist and
    :class:`AIError` when the model call fails (nothing is stored then).
    """
    task = await db.get(CrawlTask, task_id)
    if task is None:
        raise ValueError(f"Task not found: {task_id}")

    if not force:
        existing = await db.scalar(
            select(SyncDiagnosis).where(SyncDiagnosis.task_id == task_id)
        )
        if existing is not None:
            return serialize_diagnosis(existing)

    cfg = config or await get_ai_config(db)
    if not cfg.configured:
        raise AIError(not_configured_reason(cfg))

    evidence = await collect_evidence(db, task)
    client = LLMClient(cfg)
    result = await client.chat(
        build_messages(evidence),
        max_tokens=min(max(cfg.max_tokens, 1500), 4000),
        temperature=0.2,
    )
    parsed = _extract_json_object(result.text)
    if parsed is None:
        raise AIError(
            "AI 没有返回可解析的 JSON 诊断结果，请重试（或换一个指令遵循更好的模型）。"
            f"原始输出开头：{_trim(result.text, 200)}"
        )
    diagnosis = sanitize_diagnosis(parsed)
    diagnosis["proposed_changes"] = describe_changes(
        evidence.source, diagnosis["proposed_changes"]
    )

    record = {
        "status": "ok",
        "classification": diagnosis["classification"],
        "confidence": diagnosis["confidence"],
        "summary": diagnosis["summary"],
        "payload": {
            **diagnosis,
            "model": result.model or cfg.effective_model,
            "provider": cfg.provider,
            "source_name": evidence.source.get("name") or "",
            "task_error": evidence.task.get("error") or "",
            "counts": evidence.counts,
            "problem_groups": evidence.problem_groups,
            "evidence_note": (
                "证据来自任务报错、逐书失败明细与该书源规则 JSON；"
                "AI 只提建议，任何修改都需要管理员批准。"
            ),
        },
        "error": None,
        "model": result.model or cfg.effective_model,
        "tokens_used": result.tokens_used,
    }

    if not store:
        return {"task_id": task_id, "source_id": task.source, **record}

    stored = await _store(db, task, record)
    return serialize_diagnosis(stored)


async def _store(db: AsyncSession, task: CrawlTask,
                 record: dict[str, Any]) -> SyncDiagnosis:
    """Insert or replace the diagnosis row for ``task``."""
    row = await db.scalar(
        select(SyncDiagnosis).where(SyncDiagnosis.task_id == task.id)
    )
    if row is None:
        row = SyncDiagnosis(id=str(uuid4()), task_id=task.id)
        db.add(row)
    row.source_id = task.source
    row.status = record.get("status") or "ok"
    row.classification = record.get("classification")
    row.confidence = record.get("confidence")
    row.summary = record.get("summary")
    row.payload = record.get("payload")
    row.error = record.get("error")
    row.model = record.get("model")
    row.tokens_used = int(record.get("tokens_used") or 0)
    if record.get("status") == "failed":
        row.change_id = None
    await db.commit()
    await db.refresh(row)
    return row


def serialize_diagnosis(row: SyncDiagnosis) -> dict[str, Any]:
    payload = dict(row.payload or {})
    return {
        "id": row.id,
        "task_id": row.task_id,
        "source_id": row.source_id,
        "status": row.status,
        "classification": row.classification,
        "confidence": row.confidence,
        "summary": row.summary,
        "error": row.error,
        "model": row.model,
        "tokens_used": row.tokens_used,
        "change_id": row.change_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "reasoning": payload.get("reasoning") or [],
        "next_steps": payload.get("next_steps") or [],
        "proposed_changes": payload.get("proposed_changes") or [],
        "dropped_changes": int(payload.get("dropped_changes") or 0),
        "counts": payload.get("counts") or {},
        "problem_groups": payload.get("problem_groups") or [],
        "source_name": payload.get("source_name") or "",
    }


async def get_stored_diagnosis(db: AsyncSession, task_id: str) -> dict[str, Any] | None:
    row = await db.scalar(
        select(SyncDiagnosis).where(SyncDiagnosis.task_id == task_id)
    )
    return serialize_diagnosis(row) if row is not None else None


# ---------------------------------------------------------------------------
# automatic run (called by the crawl worker)
# ---------------------------------------------------------------------------


def task_deserves_diagnosis(task: CrawlTask) -> bool:
    """Only error-ish terminal tasks are worth an AI call."""
    if task.status == "failed":
        return True
    if task.status != "completed_with_errors":
        return False
    result = task.result if isinstance(task.result, dict) else {}
    return int(result.get("books_failed", 0) or 0) > 0 or \
        int(result.get("chapters_failed", 0) or 0) > 0


async def auto_diagnose_task(task_id: str, *,
                             session_factory=None) -> dict[str, Any] | None:
    """Diagnose a finished task once, if the feature is on.

    Never raises: this runs after a task has already failed, so it must not be
    able to break the worker.  Returns the diagnosis, or ``None`` when skipped.
    """
    factory = session_factory or SessionLocal
    try:
        async with factory() as db:
            task = await db.get(CrawlTask, task_id)
            if task is None or not task_deserves_diagnosis(task):
                return None
            existing = await db.scalar(
                select(SyncDiagnosis).where(SyncDiagnosis.task_id == task_id)
            )
            if existing is not None:
                return None
            cfg = await get_ai_config(db)
            if not cfg.configured or not cfg.auto_diagnose:
                return None
            logger.info("Auto AI diagnosis for failed task {} ({})", task_id, task.source)
            diagnosis = await diagnose_task(db, task_id, force=True, config=cfg)
            logger.info(
                "Auto AI diagnosis for {}: {} ({})",
                task_id, diagnosis.get("classification"), diagnosis.get("summary"),
            )
            return diagnosis
    except AIError as exc:
        logger.warning("Auto AI diagnosis for {} failed: {}", task_id, exc)
        return None
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(
            "Auto AI diagnosis for {} crashed ({}): {}",
            task_id, type(exc).__name__, exc,
        )
        return None


__all__ = [
    "CLASSIFICATIONS",
    "Evidence",
    "auto_diagnose_task",
    "build_messages",
    "build_patch",
    "collect_evidence",
    "describe_changes",
    "diagnose_task",
    "get_stored_diagnosis",
    "group_failures",
    "normalize_change_path",
    "render_config",
    "render_evidence",
    "sanitize_diagnosis",
    "serialize_diagnosis",
    "task_deserves_diagnosis",
    "value_at_path",
]
