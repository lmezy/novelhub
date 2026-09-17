"""AI assistant API endpoints.

* ``GET  /ai/status``        -- is AI configured, with which provider/model
* ``POST /ai/chat``          -- one-shot question about a book
* ``POST /ai/chat/stream``   -- the same, streamed as Server-Sent Events
* ``POST /ai/summary``       -- chapter-range summary (map-reduce)
* ``POST /ai/person``        -- character extraction
* ``POST /ai/timeline``      -- event timeline
* ``POST /ai/transform``     -- selected-text actions (explain/translate/polish)
* ``POST /ai/test``          -- admin connectivity probe
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import Book, User
from app.services.ai import AIService
from app.services.ai_client import AIError, diagnose
from app.services.ai_config import get_ai_config
from app.services.auth import get_current_user, require_admin
from app.services.visibility import ensure_book_visible


router = APIRouter(prefix="/ai", tags=["ai"])


async def _require_visible_book(db, user: User, book_id: str) -> Book:
    book = await db.get(Book, book_id)
    if not ensure_book_visible(user, book):
        raise HTTPException(status_code=404, detail="Book not found")
    return book


def _http_error(exc: Exception) -> HTTPException:
    """Translate service errors into responses the UI can show verbatim."""
    if isinstance(exc, AIError):
        status = exc.status or 502
        if status in (400, 401, 403, 404, 429):
            # Keep the upstream meaning: the admin has to fix a key/model/URL,
            # not retry.
            return HTTPException(status_code=502, detail=str(exc))
        return HTTPException(status_code=502, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=500, detail=str(exc))


class HistoryMessage(BaseModel):
    role: str = "user"
    content: str = ""


class ChatRequest(BaseModel):
    book_id: str = Field(..., description="Book to ask questions about")
    message: str = Field(..., description="User's question")
    context_chapters: int = Field(default=5, ge=1, le=20,
                                   description="Number of nearby chapters for context")
    chapter_number: int | None = Field(default=None, ge=1,
                                       description="Chapter the reader is on")
    mode: str = Field(default="auto", description="auto | rag | window")
    history: list[HistoryMessage] = Field(default_factory=list,
                                          description="Previous turns for follow-ups")


class ChatResponse(BaseModel):
    answer: str
    model: str
    tokens_used: int = 0
    provider: str = ""
    context_mode: str = ""
    sources: list[dict[str, Any]] = Field(default_factory=list)


class SummaryRequest(BaseModel):
    book_id: str = Field(..., description="Book to summarize")
    chapter_start: int | None = Field(default=None, ge=1)
    chapter_end: int | None = Field(default=None, ge=1)
    max_chapters: int = Field(default=60, ge=1, le=240)
    style: str = Field(default="detailed", description="detailed | brief")


class SummaryResponse(BaseModel):
    summary: str
    chapters_covered: int
    model: str
    total_chapters: int = 0
    chapter_start: int | None = None
    chapter_end: int | None = None
    sampled: bool = False
    provider: str = ""
    tokens_used: int = 0


class CharacterRequest(BaseModel):
    book_id: str = Field(..., description="Book to analyze characters for")
    chapter_start: int | None = Field(default=None, ge=1)
    chapter_end: int | None = Field(default=None, ge=1)


class CharacterResponse(BaseModel):
    characters: list[dict[str, Any]]
    model: str
    provider: str = ""
    parsed: bool = True
    raw: str = ""
    tokens_used: int = 0
    chapters_scanned: int = 0


class TimelineRequest(BaseModel):
    book_id: str = Field(..., description="Book to extract timeline from")
    chapter_start: int | None = Field(default=None, ge=1)
    chapter_end: int | None = Field(default=None, ge=1)


class TimelineResponse(BaseModel):
    events: list[dict[str, Any]]
    model: str
    provider: str = ""
    parsed: bool = True
    raw: str = ""
    tokens_used: int = 0
    chapters_scanned: int = 0


class TransformRequest(BaseModel):
    text: str = Field(..., description="Selected passage")
    action: str = Field(default="explain",
                        description="explain | translate | polish | continue | custom")
    book_id: str | None = None
    chapter_number: int | None = Field(default=None, ge=1)
    instruction: str = ""
    target_language: str = "中文"


class TransformResponse(BaseModel):
    action: str
    result: str
    model: str
    provider: str = ""
    tokens_used: int = 0


class AIStatusResponse(BaseModel):
    enabled: bool
    configured: bool
    available: bool
    reason: str = ""
    provider: str
    provider_label: str = ""
    provider_kind: str = ""
    model: str = ""
    effective_model: str = ""
    effective_base_url: str = ""
    rag_enabled: bool = True
    rag_top_k: int = 6
    embeddings_supported: bool = False
    effective_embedding_model: str = ""


@router.get("/status", response_model=AIStatusResponse)
async def ai_status(user: User = Depends(get_current_user),
                    db: AsyncSession = Depends(get_db)):
    """Whether the AI panel has a usable backend, and which model it will use."""
    status = await AIService(db).status()
    return {key: status.get(key) for key in AIStatusResponse.model_fields}


@router.post("/chat", response_model=ChatResponse)
async def ai_chat(payload: ChatRequest, user: User = Depends(get_current_user),
                  db: AsyncSession = Depends(get_db)):
    """Ask a question about a book with context-aware AI."""
    await _require_visible_book(db, user, payload.book_id)
    try:
        return await AIService(db).chat(
            book_id=payload.book_id,
            message=payload.message,
            chapter_number=payload.chapter_number,
            context_chapters=payload.context_chapters,
            history=[m.model_dump() for m in payload.history],
            mode=payload.mode,
        )
    except Exception as exc:
        raise _http_error(exc) from exc


async def _sse(events: AsyncIterator[dict[str, Any]]) -> AsyncIterator[str]:
    async for event in events:
        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
    yield "data: {\"type\": \"end\"}\n\n"


@router.post("/chat/stream")
async def ai_chat_stream(payload: ChatRequest, user: User = Depends(get_current_user),
                         db: AsyncSession = Depends(get_db)):
    """Streamed answer (SSE). Errors arrive as an ``error`` event."""
    await _require_visible_book(db, user, payload.book_id)
    service = AIService(db)
    events = service.stream_chat(
        book_id=payload.book_id,
        message=payload.message,
        chapter_number=payload.chapter_number,
        context_chapters=payload.context_chapters,
        history=[m.model_dump() for m in payload.history],
        mode=payload.mode,
    )
    return StreamingResponse(
        _sse(events),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # nginx sits in front of the backend in the shipped compose file;
            # without this it buffers the whole stream and the user sees the
            # answer appear all at once at the end.
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/summary", response_model=SummaryResponse)
async def ai_summary(payload: SummaryRequest, user: User = Depends(get_current_user),
                     db: AsyncSession = Depends(get_db)):
    """Generate a summary for a book or a range of chapters."""
    await _require_visible_book(db, user, payload.book_id)
    try:
        return await AIService(db).summarize(
            book_id=payload.book_id,
            chapter_start=payload.chapter_start,
            chapter_end=payload.chapter_end,
            max_chapters=payload.max_chapters,
            style=payload.style,
        )
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/person", response_model=CharacterResponse)
async def ai_characters(payload: CharacterRequest, user: User = Depends(get_current_user),
                        db: AsyncSession = Depends(get_db)):
    """Analyze and list characters in a book."""
    await _require_visible_book(db, user, payload.book_id)
    try:
        return await AIService(db).analyze_characters(
            book_id=payload.book_id,
            chapter_start=payload.chapter_start,
            chapter_end=payload.chapter_end,
        )
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/timeline", response_model=TimelineResponse)
async def ai_timeline(payload: TimelineRequest, user: User = Depends(get_current_user),
                      db: AsyncSession = Depends(get_db)):
    """Extract a timeline of events from a book."""
    await _require_visible_book(db, user, payload.book_id)
    try:
        return await AIService(db).extract_timeline(
            book_id=payload.book_id,
            chapter_start=payload.chapter_start,
            chapter_end=payload.chapter_end,
        )
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/transform", response_model=TransformResponse)
async def ai_transform(payload: TransformRequest, user: User = Depends(get_current_user),
                       db: AsyncSession = Depends(get_db)):
    """Explain / translate / polish a passage selected while reading."""
    if payload.book_id:
        await _require_visible_book(db, user, payload.book_id)
    try:
        return await AIService(db).transform(
            text=payload.text,
            action=payload.action,
            book_id=payload.book_id,
            chapter_number=payload.chapter_number,
            instruction=payload.instruction,
            target_language=payload.target_language,
        )
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/transform/stream")
async def ai_transform_stream(payload: TransformRequest,
                              user: User = Depends(get_current_user),
                              db: AsyncSession = Depends(get_db)):
    """Streamed selected-text action (same contract as ``/ai/chat/stream``)."""
    if payload.book_id:
        await _require_visible_book(db, user, payload.book_id)
    events = AIService(db).stream_transform(
        text=payload.text,
        action=payload.action,
        book_id=payload.book_id,
        chapter_number=payload.chapter_number,
        instruction=payload.instruction,
        target_language=payload.target_language,
    )
    return StreamingResponse(
        _sse(events),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


class AITestRequest(BaseModel):
    test_embeddings: bool = False


# ---------------------------------------------------------------------------
# Sync-failure diagnosis
#
# Admin-only: the answer quotes the book source's rules and the proposal it can
# produce is applied through the normal approval flow, never automatically.
# ---------------------------------------------------------------------------


class DiagnoseRequest(BaseModel):
    force: bool = Field(default=False, description="Re-run even if one is stored")


@router.get("/diagnose/{task_id}")
async def get_task_diagnosis(task_id: str,
                             user: User = Depends(require_admin),
                             db: AsyncSession = Depends(get_db)):
    """The stored AI diagnosis of a sync task (404 when not analysed yet)."""
    from app.services.ai_diagnosis import get_stored_diagnosis

    stored = await get_stored_diagnosis(db, task_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="这个任务还没有 AI 诊断结果")
    return stored


@router.post("/diagnose/{task_id}")
async def run_task_diagnosis(task_id: str,
                             payload: DiagnoseRequest | None = None,
                             user: User = Depends(require_admin),
                             db: AsyncSession = Depends(get_db)):
    """Explain a failed sync task; stores and returns the diagnosis."""
    from app.services.ai_diagnosis import diagnose_task

    try:
        return await diagnose_task(
            db, task_id, force=bool(payload and payload.force)
        )
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/diagnose/{task_id}/propose")
async def propose_source_change(task_id: str,
                                user: User = Depends(require_admin),
                                db: AsyncSession = Depends(get_db)):
    """Turn the diagnosis' suggested changes into a pending source proposal.

    The proposal is *not* applied here: it shows up in 设置 → 审批, where an
    admin reviews the diff and approves or rejects it.
    """
    from uuid import uuid4

    from sqlalchemy import select

    from app.models import Source, SourceChange, SyncDiagnosis
    from app.services.ai_diagnosis import PROPOSAL_ORIGIN, build_patch

    row = await db.scalar(
        select(SyncDiagnosis).where(SyncDiagnosis.task_id == task_id)
    )
    if row is None:
        raise HTTPException(status_code=404, detail="这个任务还没有 AI 诊断结果")

    if row.change_id:
        existing = await db.get(SourceChange, row.change_id)
        if existing is not None and existing.status == "pending":
            return {
                "change_id": existing.id,
                "status": existing.status,
                "created": False,
                "message": "这个诊断已经提交过提案，去「设置 → 审批」处理。",
            }

    changes = [
        change for change in ((row.payload or {}).get("proposed_changes") or [])
        if isinstance(change, dict) and change.get("path") and change.get("new") is not None
    ]
    if not changes:
        raise HTTPException(
            status_code=400,
            detail="这次诊断没有可提交的配置修改建议（多半是站点侧问题，改配置解决不了）。",
        )

    source = await db.get(Source, row.source_id) if row.source_id else None
    if source is None:
        raise HTTPException(status_code=404, detail="书源不存在，无法生成提案")

    change = SourceChange(
        id=str(uuid4()),
        user_id=user.id,
        action="update",
        source_id=row.source_id,
        source_data={
            "patch": build_patch(changes),
            "diff": changes,
            "origin": PROPOSAL_ORIGIN,
            "diagnosis_id": row.id,
            "task_id": task_id,
            "classification": row.classification,
            "summary": row.summary,
        },
        status="pending",
    )
    db.add(change)
    row.change_id = change.id
    await db.commit()

    return {
        "change_id": change.id,
        "status": change.status,
        "created": True,
        "changes": len(changes),
        "message": "已生成待审批提案，去「设置 → 审批」确认后才会写入书源。",
    }


@router.post("/test", dependencies=[Depends(require_admin)])
async def ai_test(payload: AITestRequest | None = None,
                  db: AsyncSession = Depends(get_db)):
    """Probe the configured provider.

    The admin UI uses ``POST /admin/ai/test`` (same behaviour, next to the
    settings form); this alias stays for API clients.
    """
    cfg = await get_ai_config(db)
    return await diagnose(cfg, test_embeddings=bool(payload and payload.test_embeddings))
