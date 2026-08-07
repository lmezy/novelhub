from app.services.book_enrichment import (
    analyze_book_text,
    enrich_book_metadata,
    extract_status,
)


def test_analyze_book_text_extracts_metadata_tags_and_r18():
    text = (
        "书名：测试书\n"
        "作者：作者A\n"
        "简介：都市修仙小说\n"
        "第一章 开始\n"
        "正文包含色情内容\n"
    )

    result = analyze_book_text(text)

    assert result["title"] == "测试书"
    assert result["author"] == "作者A"
    assert result["description"] == "都市修仙小说"
    assert "都市" in result["tags"]
    assert "武侠" in result["tags"]
    assert result["is_r18"] is True
    assert "都市" in result["categories"]


def test_enrich_book_metadata_merges_explicit_and_suggested_tags():
    result = enrich_book_metadata(
        meta={"title": "已知书", "tags": ["都市"]},
        chapters=[("第一章", "主角穿越到现代都市开始修仙")],
    )

    assert result["title"] == "已知书"
    assert "都市" in result["tags"]
    assert "穿越" in result["tags"]
    assert result["is_r18"] is False
    assert "都市" in result["categories"]


def test_extract_status_recognizes_completed_text():
    assert extract_status("这是一本已完结的小说") == "completed"
    assert extract_status("本书连载中") == "ongoing"
