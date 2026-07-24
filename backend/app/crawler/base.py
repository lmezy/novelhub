from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class RemoteChapter:
    source_chapter_id: str
    title: str
    url: str
    chapter_number: int


@dataclass(frozen=True)
class RemoteBook:
    source_book_id: str
    title: str
    author: str
    description: str | None
    status: str | None
    chapters: list[RemoteChapter]


class NovelSourcePlugin(Protocol):
    name: str

    async def fetch_book(self, url: str) -> RemoteBook:
        ...

    async def fetch_chapter_content(self, chapter: RemoteChapter) -> str:
        ...
