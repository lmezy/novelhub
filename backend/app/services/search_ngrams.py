"""Index-time n-grams: the exact CJK substring match Meilisearch cannot do.

Meilisearch analyses Chinese one character at a time (charabia), so the query
language can only ask "does this document contain 铃 *or* 铛" -- never "does it
contain 铃铛".  NovelHub scores candidates in Python instead, which means a
result can only ever be found if the *engine* ranked it inside the window being
scanned.  That window is the ceiling users hit: ``pagination.maxTotalHits``
(10 000) plus the per-search candidate windows, so a query matching 12 466 books
silently reported 10 000 and could not be paged past it.

The fix is to hand the engine a derived field it *can* filter on.  Every indexed
document carries the set of its distinct ``GRAM_SIZE``-character substrings
("grams") of the short, high-value text fields -- book title, book author,
chapter title, chapter's book title, chapter's book author.  A query then turns
into an ordinary filter:

    铃铛    ->  title_grams = "铃铛"
    白骨精  ->  title_grams = "白骨" AND title_grams = "骨精"

The engine returns *every* document holding those grams: no ranking cut, no
window, and an honest count.  A query is narrowed with the longest gram it can
produce, so a two-character query is exact; a longer one is a superset (a
document may hold 白骨 and 骨精 in unrelated places), and the caller still
verifies the contiguous substring with the existing scorer before serving a
page.  A one-character query is narrowed with that single character, which is
what keeps the broadest searches -- 的 matches 12 466 books on the live index --
from falling back to a truncated window.

A **fuzzy** condition narrows differently, because it means something else:
"every character of the query appears somewhere, in any order, gaps allowed".
An AND over the query's distinct characters says exactly that, so fuzzy is
narrowed by its characters (a 1-gram each) rather than by adjacency.  That case
is not an afterthought -- the library search box sends ``fuzzy`` for
title/author.

Normalisation deliberately mirrors ``SearchService._condition_score``, which
transforms its two sides *differently*: the field (haystack) is
``" ".join(list).lower()`` and is not stripped, while the query (needle) is
``value.strip().lower()``.  ``normalize_field`` and ``normalize_query`` keep
that split, because collapsing it would make a query of ``" 铃铛 "`` demand the
grams of its own surrounding spaces and filter away every real hit.

Only short fields are grammed.  ``description`` is allowed to reach 2 000
characters and ``content`` 100 000, which would be thousands of grams per
document across 150 000 documents; the fields above are the ones that are both
short and heavily searched.  ``tags``/``category_names`` are excluded too: they
are short enumerated values where the existing window already covers the
catalogue, and their scoring semantics (substring of the space-joined list)
would not survive an exact ``=`` filter unchanged.
"""

from __future__ import annotations

from typing import Any, Iterable

__all__ = [
    "GRAM_FIELD_FOR_ATTR",
    "GRAM_FIELD_FOR_BOOK",
    "GRAM_FIELD_FOR_CHAPTER",
    "GRAM_SIZE",
    "GRAM_SIZES",
    "book_gram_fields",
    "chapter_gram_fields",
    "field_grams",
    "fuzzy_clauses",
    "gram_clauses",
    "gram_field_for",
    "gram_filter",
    "normalize_field",
    "normalize_query",
    "query_gram_size",
    "query_grams",
]

#: The gram lengths the derived field carries.  Two is the shortest substring a
#: CJK reader thinks of as a word fragment (铃铛) and keeps the field small -- a
#: 10-character title yields at most 9 of them.  Single characters are carried
#: too so that a one-character query is narrowed the same way: 的 matches 12 466
#: books on the live index, which is well past the 10 000-candidate window that
#: a single-character search had to fall back to, and falling back would leave
#: exactly the truncation this module exists to remove.  Both sizes together are
#: still under 20 tokens for a title.
GRAM_SIZES = (1, 2)
#: The longest gram a query is narrowed with.
GRAM_SIZE = max(GRAM_SIZES)

#: ``attr -> derived filterable attribute``.  ``title`` is the book title in the
#: books index and the chapter title in the chapters index; the name is shared
#: on purpose, because the grams are computed from that index's own ``title``.
GRAM_FIELD_FOR_ATTR: dict[str, str] = {
    "title": "title_grams",
    "author": "author_grams",
    "book_title": "book_title_grams",
    "book_author": "book_author_grams",
}

#: Derived attributes each index declares as filterable (and writes).
GRAM_FIELD_FOR_BOOK = ("title_grams", "author_grams")
GRAM_FIELD_FOR_CHAPTER = ("title_grams", "book_title_grams", "book_author_grams")


def normalize_field(text: Any) -> str:
    """Normalise an **indexed field value** the way ``_condition_score`` does.

    ``_condition_score`` builds its haystack as ``" ".join(list).lower()`` and
    does *not* strip it, so a gram computed from anything else would drift from
    the test the grams exist to accelerate.
    """
    if isinstance(text, (list, tuple)):
        text = " ".join(str(item) for item in text)
    return (text or "").lower()


def normalize_query(value: Any) -> str:
    """Normalise a **search query** the way ``_condition_score`` does.

    The needle side is ``value.strip().lower()`` -- notably *not* the same
    transformation as the field side.  A query of ``" 铃铛 "`` is looked for as
    ``铃铛``; if the grams were taken before stripping, the filter would demand
    the grams of ``" 铃铛 "`` (including the spaces) from a document that stores
    ``铃铛``, and every hit would be filtered away.
    """
    if isinstance(value, (list, tuple)):
        value = " ".join(str(item) for item in value)
    return str(value or "").strip().lower()


def field_grams(text: Any) -> list[str]:
    """Every gram of an indexed field value, across all :data:`GRAM_SIZES`.

    Sorted (not insertion-ordered) so the filter a query produces is
    deterministic: a stable filter string lets the engine reuse its own cached
    answer across identical searches.
    """
    normalised = normalize_field(text)
    collected: set[str] = set()
    for size in GRAM_SIZES:
        collected.update(_grams_of(normalised, size))
    return sorted(collected)


def query_gram_size(value: Any) -> int:
    """Which gram length a query is narrowed with (``0`` when it has none).

    The *longest* gram the query can produce is also the most selective one, so
    a 2-character query uses its bigram rather than the AND of two single
    characters; a one-character query has only the single character.
    """
    length = len(normalize_query(value))
    return min(length, GRAM_SIZE)


def query_grams(value: Any) -> list[str]:
    """The grams a query is narrowed with (empty when it is too short)."""
    size = query_gram_size(value)
    if size < 1:
        return []
    return _grams_of(normalize_query(value), size)


def _grams_of(normalised: str, size: int) -> list[str]:
    if size < 1 or len(normalised) < size:
        return []
    return sorted({normalised[i : i + size] for i in range(len(normalised) - size + 1)})


def gram_field_for(attr: str) -> str | None:
    """The derived attribute holding the grams of ``attr`` (``None`` if ungrammed)."""
    return GRAM_FIELD_FOR_ATTR.get(attr)


def _quote(value: str) -> str:
    """Wrap a filter value in double quotes, escaping what Meilisearch documents.

    A backslash must be escaped before the quote, otherwise ``a\\"b`` would be
    written as ``a\\\\"b`` and terminate the string in the wrong place.
    """
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def gram_clauses(field: str, value: str) -> list[str]:
    """One ``field = "gram"`` clause per gram of the query (empty if none)."""
    return [f"{field} = {_quote(gram)}" for gram in query_grams(value)]


def fuzzy_clauses(field: str, value: str) -> list[str]:
    """One clause per distinct character of the query.

    Fuzzy scoring (``_condition_score`` in ``fuzzy`` mode) only requires every
    character of the query to appear somewhere in the field -- any order, gaps
    allowed, whitespace ignored.  An AND over the query's distinct non-space
    characters is therefore **exactly** that test, not a superset of it, and the
    1-grams in the field are what make it expressible as a filter.

    This matters more than it looks: the library search box sends ``fuzzy`` for
    title/author (``fuzzyFields`` in ``BooksPage.vue``), so narrowing only the
    ``exact`` mode would have left the most-used search on its truncated window.
    """
    characters = sorted({ch for ch in normalize_query(value) if not ch.isspace()})
    return [f"{field} = {_quote(ch)}" for ch in characters]


def gram_filter(field: str, value: str, mode: str = "exact") -> str | None:
    """The filter expression selecting every document that may contain ``value``.

    ``exact`` narrows by the query's grams (adjacency; a superset for queries
    longer than one gram, so the caller still verifies the substring), ``fuzzy``
    by its distinct characters (exact by construction).

    ``None`` when the query produces no clause at all -- an empty query -- because
    an absent filter would mean "every document" and the caller would read that
    as "no narrowing at all".
    """
    clauses = (
        fuzzy_clauses(field, value) if mode == "fuzzy"
        else gram_clauses(field, value)
    )
    if not clauses:
        return None
    if len(clauses) == 1:
        return clauses[0]
    return " AND ".join(f"({clause})" for clause in clauses)


def _gram_documents(doc: dict, attrs: Iterable[str]) -> dict:
    """``{gram field: [grams]}`` for one document, from its own fields.

    Shared by the live indexing path and the backfill so the two cannot produce
    different grams for the same document.
    """
    result: dict[str, list[str]] = {}
    for attr in attrs:
        field = gram_field_for(attr)
        if field is None:
            continue
        result[field] = field_grams(doc.get(attr))
    return result


def book_gram_fields(doc: dict) -> dict[str, list[str]]:
    """Gram fields for a ``books`` document."""
    return _gram_documents(doc, ("title", "author"))


def chapter_gram_fields(doc: dict) -> dict[str, list[str]]:
    """Gram fields for a ``chapters`` document."""
    return _gram_documents(doc, ("title", "book_title", "book_author"))
