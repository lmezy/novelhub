"""Book-title normalisation shared by the "other sources" queries.

The important properties are (a) the Python form the API compares with and the
SQL form the database filters with use the *same* pattern, and (b) that pattern
covers the whitespace both engines disagree about -- Python's ``\\s`` matches
NBSP, Postgres' ``[[:space:]]`` does not, and titles in this library really do
contain NBSP (4 of 23,979 rows did).
"""

import re

from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.models import Book
from app.services.book_title import (
    TITLE_NOISE_PATTERN,
    normalize_title,
    normalized_title_sql,
)


def test_normalize_title_strips_brackets_quotes_and_spaces():
    assert normalize_title("《剑来》") == "剑来"
    assert normalize_title("[作者] 剑来") == "作者剑来"
    assert normalize_title("'剑来'") == "剑来"
    assert normalize_title("剑来（全本）") == "剑来全本"
    assert normalize_title("  剑来\t\n") == "剑来"
    assert normalize_title("Jian Lai") == "jianlai"
    assert normalize_title(None) == ""
    assert normalize_title("") == ""


def test_normalize_title_strips_every_unicode_space_python_knows():
    """``\\xa0`` (NBSP) is the one that actually bit us: PG's ``\\s`` misses it."""
    for char in ("\xa0", "\u3000", "\u2000", "\u202f", "\u205f", "\u1680", "\x85"):
        assert normalize_title(f"剑{char}来") == "剑来", repr(char)


def test_pattern_escapes_the_class_delimiters():
    # A bare ``]`` would end the character class on both engines.
    assert r"\[" in TITLE_NOISE_PATTERN
    assert r"\]" in TITLE_NOISE_PATTERN
    assert re.compile(TITLE_NOISE_PATTERN)


def test_sql_expression_mirrors_the_python_normaliser():
    expression = normalized_title_sql(Book.title)
    rendered = str(expression)

    assert "regexp_replace" in rendered
    assert "lower(books.title)" in rendered
    # The pattern (and the 'g' flag) travel as bound parameters, so nothing in
    # it -- including the quote characters -- has to be escaped for Postgres.
    assert TITLE_NOISE_PATTERN not in rendered

    statement = select(Book.id).where(normalized_title_sql(Book.title) == "剑来")
    compiled = statement.compile(dialect=postgresql.dialect())
    values = list(compiled.params.values())
    assert TITLE_NOISE_PATTERN in values
    assert "g" in values
    assert "剑来" in values
