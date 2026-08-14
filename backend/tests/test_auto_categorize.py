from app.services.auto_categorize import classify_category_names


def test_categories_prefer_source_tags_over_synopsis_noise():
    result = classify_category_names(
        ["奇幻", "后宫", "异世界"],
        title="异世界冒险",
        description="主角来到现代都市，开始新的生活。",
        is_r18=True,
    )

    assert result[0] == "奇幻"
    assert "都市" not in result


def test_categories_keep_explicit_r18_facets():
    result = classify_category_names(
        ["都市", "调教", "反差"],
        is_r18=True,
    )

    assert result == ["都市", "调教", "反差"]
