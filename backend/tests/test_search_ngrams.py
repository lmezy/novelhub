"""The gram contract: what the engine filter promises, and what it must not.

The filter is only allowed to be a **superset** of the scorer's answer.  If it
ever missed a document the scorer would have accepted, the search would lose
results the old windowed scan used to find -- a regression disguised as an
optimisation.  The property test near the bottom is the one that matters; the
rest pin the individual pieces that property depends on.
"""

import pytest

from app.services.search import SearchService
from app.services.search_ngrams import (
    GRAM_FIELD_FOR_BOOK,
    GRAM_FIELD_FOR_CHAPTER,
    GRAM_SIZE,
    GRAM_SIZES,
    book_gram_fields,
    chapter_gram_fields,
    field_grams,
    fuzzy_clauses,
    gram_clauses,
    gram_field_for,
    gram_filter,
    normalize_field,
    normalize_query,
    query_gram_size,
    query_grams,
)


def test_two_character_query_is_narrowed_by_its_bigram():
    assert query_grams("铃铛") == ["铃铛"]


def test_one_character_query_is_narrowed_by_that_character():
    """A single character is still narrowed, so the broadest query has no window.

    的 matches 12 466 books on the live index; falling back to the 10 000-wide
    candidate window for it would leave exactly the truncation this removes.
    """
    assert query_grams("铃") == ["铃"]
    assert query_grams("的") == ["的"]
    assert query_gram_size("铃") == 1
    assert query_gram_size("铃铛") == 2
    assert query_gram_size("铃铛录") == 2


def test_longer_query_yields_every_adjacent_pair():
    assert query_grams("白骨精") == ["白骨", "骨精"]


def test_repeated_characters_collapse_to_one_gram():
    assert query_grams("aaa") == ["aa"]


def test_empty_query_has_no_grams():
    assert query_grams("") == []
    assert query_grams("   ") == []
    assert query_grams(None) == []
    assert query_gram_size("") == 0


def test_field_grams_carry_both_single_characters_and_pairs():
    """The field has to hold every length a query might be narrowed with."""
    assert field_grams("铃铛") == ["铃", "铃铛", "铛"]
    assert field_grams("a") == ["a"]


def test_field_and_query_normalisation_match_the_scorer():
    """``_condition_score`` transforms its two sides differently, and so must we.

    The haystack is ``" ".join(list).lower()`` and is never stripped; the needle
    is ``value.strip().lower()``.  Using one transformation for both would make
    a query of ``" 铃铛 "`` ask the engine for the grams of its own padding and
    match nothing.
    """
    assert normalize_field("ABC") == "abc"
    assert normalize_field(["言情", "都市"]) == "言情 都市"
    assert normalize_field(None) == ""
    assert normalize_field("  空格 ") == "  空格 "

    assert normalize_query("  ABC  ") == "abc"
    assert normalize_query(["言情", "都市"]) == "言情 都市"
    assert normalize_query(None) == ""


@pytest.mark.parametrize(
    ("value", "text"),
    [
        ("铃铛", "【小铃铛】"),
        ("铃铛", "铃木同学"),
        ("abc", "ABC"),
        (" 铃铛 ", "铃铛"),
    ],
)
def test_normalised_needle_in_haystack_is_the_scorers_own_test(value, text):
    """``score > 0`` must mean exactly "the normalised needle is in the haystack".

    Only strings on both sides: the scored *value* is always a string (the API
    schema is ``value: str`` and the route strips it), and ``_condition_score``
    itself would raise on a list needle.  Lists only ever appear as the field
    side (``tags``/``category_names``), which the storage test below covers.
    """
    accepted = SearchService._condition_score(value, text, "exact") > 0
    manual = normalize_query(value) in normalize_field(text)
    assert accepted == manual
    assert manual == (normalize_query(value) != "" and normalize_query(value) in text.lower())


def test_list_field_values_are_scored_as_a_space_joined_string():
    """``tags``/``category_names`` arrive as lists; the scorer joins them."""
    assert SearchService._condition_score("言情", ["言情", "都市"], "exact") > 0
    assert SearchService._condition_score("言情 都市", ["言情", "都市"], "exact") > 0
    assert SearchService._condition_score("情都", ["言情", "都市"], "exact") == 0
    assert normalize_field(["言情", "都市"]) == "言情 都市"


def test_filter_for_a_one_character_query_is_an_exact_match():
    assert gram_filter("title_grams", "的") == 'title_grams = "的"'


def test_filter_for_a_two_character_query_is_an_exact_match():
    assert gram_filter("title_grams", "铃铛") == 'title_grams = "铃铛"'


def test_filter_for_a_longer_query_ands_every_gram():
    assert gram_filter("title_grams", "白骨精") == (
        '(title_grams = "白骨") AND (title_grams = "骨精")'
    )


def test_filter_is_none_only_when_the_query_is_empty():
    """``None`` means "we cannot narrow this", never "match everything".

    Returning an empty string for an empty query would be read by the engine as
    no filter at all, and the caller would treat every document as a candidate.
    """
    assert gram_filter("title_grams", "") is None
    assert gram_filter("title_grams", "   ") is None
    assert gram_clauses("title_grams", "") == []


def test_query_padding_is_ignored_by_the_filter():
    """The exact bug the split normalisation exists to prevent."""
    assert gram_filter("title_grams", " 铃铛 ") == gram_filter("title_grams", "铃铛")


def test_filter_escapes_quotes_and_backslashes():
    assert gram_filter("title_grams", 'a"') == 'title_grams = "a\\""'
    assert gram_filter("title_grams", "a\\") == 'title_grams = "a\\\\"'


def test_filter_is_deterministic_for_the_same_query():
    """A stable filter string lets the engine reuse its own query cache."""
    assert gram_filter("title_grams", "白骨精") == gram_filter("title_grams", "白骨精")


def test_only_short_text_fields_are_grammed():
    """``description``/``content``/array fields must stay on the window path.

    Gramming a 100 000-character chapter body would be thousands of tokens per
    document across the whole index; the score for ``tags`` is a substring of
    the space-joined list, which an exact ``=`` filter would not reproduce.
    """
    assert gram_field_for("title") == "title_grams"
    assert gram_field_for("author") == "author_grams"
    assert gram_field_for("book_title") == "book_title_grams"
    assert gram_field_for("book_author") == "book_author_grams"
    for attr in ("description", "book_description", "content", "tags", "category"):
        assert gram_field_for(attr) is None


def test_book_and_chapter_gram_fields_cover_their_own_attributes():
    book = book_gram_fields({"title": "铃铛", "author": "张三", "description": "x" * 50})
    assert book == {"title_grams": field_grams("铃铛"), "author_grams": field_grams("张三")}
    # The description is deliberately not grammed; it is only ever scored
    # through the window.
    assert not any("description" in key for key in book)

    chapter = chapter_gram_fields(
        {"title": "第一章", "book_title": "铃铛", "book_author": "张三", "content": "y"}
    )
    assert chapter == {
        "title_grams": field_grams("第一章"),
        "book_title_grams": field_grams("铃铛"),
        "book_author_grams": field_grams("张三"),
    }


def test_index_gram_field_lists_match_the_document_helpers():
    """The declared filterable fields must be exactly the ones being written.

    A field written but not filterable is a filter the engine rejects at query
    time; a field declared but never written is a filter that always returns
    nothing.  Neither shows up until a live search, so pin them together.
    """
    assert set(GRAM_FIELD_FOR_BOOK) == set(book_gram_fields({"title": "a", "author": "b"}))
    assert set(GRAM_FIELD_FOR_CHAPTER) == set(
        chapter_gram_fields({"title": "a", "book_title": "b", "book_author": "c"})
    )


CORPUS = [
    "【小铃铛】",
    "铃木同学",
    "聖誕鈴聲",
    "白骨肿么破",
    "白骨精新传",
    "骨精与白骨",
    "剑来",
    "剑归来",
    "铃 铛",
    "ABC",
    "  空格 前缀",
]


@pytest.mark.parametrize(
    "query",
    [
        "铃铛", "白骨精", "剑来", "骨精", "铃 铛", "ABC", "白", "的",
        "", "   ", "不存在的词", " 铃铛 ", "空格",
    ],
)
def test_gram_filter_never_excludes_a_document_the_scorer_accepts(query):
    """The one invariant that makes this safe to ship.

    For every document the scorer calls an exact hit, the gram filter must also
    select it.  (The reverse is allowed: the filter is a superset, which is why
    the caller still verifies the substring in Python.)
    """
    expression = gram_filter("title_grams", query)
    if expression is None:
        # Only an empty query has no gram to narrow with.
        assert query_grams(query) == []
        return

    required = {
        clause.split('"')[1]
        for clause in expression.replace("(", "").replace(")", "").split(" AND ")
    }
    assert required == set(query_grams(query))
    assert required

    for text in CORPUS:
        accepted = SearchService._condition_score(query, text, "exact") > 0
        selected = required <= set(field_grams(text))
        assert not accepted or selected, (
            f"scorer accepts {text!r} for {query!r} but the gram filter drops it"
        )


def test_fuzzy_filter_ands_the_distinct_characters():
    assert gram_filter("title_grams", "铃铛", "fuzzy") == (
        '(title_grams = "铃") AND (title_grams = "铛")'
    )
    # Whitespace is ignored by fuzzy scoring, so it must be ignored here too --
    # the field's grams would otherwise have to contain the space.
    assert gram_filter("title_grams", "铃 铛", "fuzzy") == (
        '(title_grams = "铃") AND (title_grams = "铛")'
    )
    # Repeated characters collapse: the test is "every character appears", once.
    assert gram_filter("title_grams", "铃铃铛", "fuzzy") == (
        '(title_grams = "铃") AND (title_grams = "铛")'
    )


@pytest.mark.parametrize(
    ("query", "text"),
    [
        ("铃铛", "【小铃铛】"),
        ("铃铛", "铛的一声...铃"),
        ("白骨精", "精骨白"),
        ("白骨精", "白骨"),
        ("铃 铛", "铃铛"),
        ("abc", "cba"),
        ("abc", "ab"),
    ],
)
def test_fuzzy_filter_is_exactly_the_scorers_gate(query, text):
    """Fuzzy narrowing is an equivalence, not a superset.

    ``_condition_score`` in fuzzy mode asks only that every character appear
    somewhere, which is precisely what an AND over the distinct characters
    selects -- so unlike the exact mode there is nothing left for Python to
    discard.
    """
    expression = gram_filter("title_grams", query, "fuzzy")
    if expression is None:
        assert fuzzy_clauses("title_grams", query) == []
        return
    required = {
        clause.split('"')[1]
        for clause in expression.replace("(", "").replace(")", "").split(" AND ")
    }
    accepted = SearchService._condition_score(query, text, "fuzzy") > 0
    selected = required <= set(field_grams(text))
    assert accepted == selected, (
        f"fuzzy scorer and filter disagree about {text!r} for {query!r}"
    )


def test_gram_sizes_are_one_and_two():
    """Every other assertion here is written against 1- and 2-gram fields."""
    assert GRAM_SIZES == (1, 2)
    assert GRAM_SIZE == 2
