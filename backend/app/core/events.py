from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Callable

from loguru import logger


class EventType(StrEnum):
    BOOK_CREATED = "book.created"
    BOOK_UPDATED = "book.updated"
    BOOK_DELETED = "book.deleted"
    CHAPTER_CREATED = "chapter.created"
    CHAPTER_UPDATED = "chapter.updated"
    CHAPTER_DELETED = "chapter.deleted"
    SYNC_STARTED = "sync.started"
    SYNC_COMPLETED = "sync.completed"
    SYNC_FAILED = "sync.failed"
    COOKIE_EXPIRED = "cookie.expired"


@dataclass
class Event:
    type: EventType
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


Listener = Callable[[Event], None]


class EventBus:
    _listeners: dict[EventType, list[Listener]] = {}

    @classmethod
    def subscribe(cls, event_type: EventType, listener: Listener) -> None:
        cls._listeners.setdefault(event_type, []).append(listener)

    @classmethod
    def publish(cls, event: Event) -> None:
        listeners = cls._listeners.get(event.type, [])
        if not listeners:
            return
        for listener in listeners:
            try:
                listener(event)
            except Exception:
                logger.opt(exception=True).error("Event listener failed for {}", event.type)


def emit(event_type: EventType, **data: Any) -> None:
    EventBus.publish(Event(type=event_type, data=data))
