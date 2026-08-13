from __future__ import annotations

import pytest

from app.services.text_extract import extract_text


def test_empty_input():
    result = extract_text(None)
    assert result.body is None
    assert result.character_count == 0
    assert result.hashtags == []
    assert result.mentions == []


def test_hashtags_and_mentions_are_separated():
    result = extract_text("Loving #Blender and #b3d — thanks @BlenderFoundation!")
    assert result.hashtags == ["Blender", "b3d"]
    assert result.mentions == ["BlenderFoundation"]


def test_line_breaks_and_emoji_are_preserved():
    """Tier 2 requires the caption to survive intact, not normalised."""
    body = "Line one\n\nLine two 🎬🔥\nLine three"
    result = extract_text(body)
    assert result.body == body
    assert "\n\n" in result.body
    assert "🎬" in result.body


def test_character_count_matches_what_the_author_sees():
    body = "Hello 🎬\nWorld"
    assert extract_text(body).character_count == len(body)


def test_duplicates_are_removed_keeping_the_first_spelling():
    result = extract_text("#Travel #travel #TRAVEL @same @Same")
    assert result.hashtags == ["Travel"]
    assert result.mentions == ["same"]


def test_non_latin_hashtags_are_supported():
    result = extract_text("#سفر #旅行 #путешествие")
    assert result.hashtags == ["سفر", "旅行", "путешествие"]


def test_trailing_punctuation_is_not_captured():
    result = extract_text("Ask @someone. Or #hashtag, maybe #another!")
    assert result.mentions == ["someone"]
    assert result.hashtags == ["hashtag", "another"]


@pytest.mark.parametrize(
    "body",
    [
        "Visit https://example.com/page#section for more",
        "Email me at name@example.com",
        "Use &#39; for an apostrophe",
    ],
)
def test_urls_entities_and_emails_are_not_mistaken_for_tags(body):
    result = extract_text(body)
    assert result.hashtags == []
    assert result.mentions == []


def test_underscores_and_digits_are_part_of_a_tag():
    result = extract_text("#open_movie_2014 @user_123")
    assert result.hashtags == ["open_movie_2014"]
    assert result.mentions == ["user_123"]


def test_dotted_mention_is_kept_whole():
    """Instagram handles routinely contain dots."""
    assert extract_text("@peach.project made it").mentions == ["peach.project"]
