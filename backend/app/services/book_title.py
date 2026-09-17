"""Normalised book titles, shared by every "same book, other sources" query.

``books.title`` keeps whatever decoration the site used -- ``《剑来》``,
``[作者] 剑来``, full-width brackets, non-breaking spaces -- so finding the same
book on another source means comparing *normalised* titles.

That comparison used to run in Python over every row of ``books``: the query
loaded all 23,979 books with the model's eager ``tags`` (lazy="joined"),
``categories`` and ``custom_tags`` (selectin) loads, and then compared titles in
a list comprehension.  On the production library that took **10.3 s per call**,
and the same helper runs once for every global book sync (so it was also
burning crawler CPU).

The pattern below is the single definition used by both sides: Python
(:func:`normalize_title`) and SQL (:func:`normalized_title_sql`).  Postgres is
asked for exactly the same match, so the database only returns the handful of
rows that can possibly match and Python never sees the whole table again.

Two engines, one pattern -- the differences that matter:

* Python's ``\\s`` is Unicode-aware and matches ``\\xa0`` (NBSP); Postgres'
  ``\\s`` is ``[[:space:]]`` and does **not**.  Every non-ASCII space is
  therefore listed literally in the pattern, and ``\\s`` is kept only for the
  ASCII whitespace both engines agree on.
* Both engines understand ``\\[`` / ``\\]`` inside a character class.
"""

import re
from typing import Any

__all__ = [
    "TITLE_NOISE_PATTERN",
    "normalize_title",
    "normalized_title_sql",
]


#: Brackets/quotes that carry no meaning when matching two titles together.
_TITLE_NOISE_CHARS = "《》「」『』〈〉（）【】[]\"'“”‘’"

#: Whitespace that Python's ``\s`` matches but Postgres' ``[[:space:]]`` does
#: not (at least under the ``en_US.utf8`` collation this deployment uses).  They
#: are spelled out as real characters so both engines strip them.
_UNICODE_SPACES = (
    "\x1c\x1d\x1e\x1f\x85\xa0"
    "\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    "\u2028\u2029\u202f\u205f\u3000"
)


def _class_body(chars: str) -> str:
    """Escape the characters that are special inside a ``[...]`` class."""
    return "".join("\\" + char if char in "[]\\^-" else char for char in chars)


#: Matches everything :func:`normalize_title` strips before comparing.
TITLE_NOISE_PATTERN = (
    "[" + _class_body(_TITLE_NOISE_CHARS) + _UNICODE_SPACES + r"\s]+"
)

_PYTHON_PATTERN = re.compile(TITLE_NOISE_PATTERN)


def normalize_title(title: str | None) -> str:
    """Fold a book title to the form two sources' copies are compared in."""
    return _PYTHON_PATTERN.sub("", title or "").lower()


def normalized_title_sql(column: Any) -> Any:
    """``regexp_replace(lower(column), <pattern>, '', 'g')`` as a SQLAlchemy expression.

    The pattern travels as a bound parameter, so no quoting/escaping of the
    pattern (which contains both quote characters) can go wrong.  A functional
    index on the same expression would need the pattern inlined verbatim; see
    ``docs/full-site-sync.md`` if this ever needs to go below the current
    ~100 ms full scan.
    """
    from sqlalchemy import func

    return func.regexp_replace(func.lower(column), TITLE_NOISE_PATTERN, "", "g")
