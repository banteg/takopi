import re

from takopi.telegram.render import render_markdown, split_markdown_body


def test_render_markdown_basic_entities() -> None:
    text, entities = render_markdown("**bold** and `code`")

    assert text == "bold and code\n\n"
    assert entities == [
        {"type": "bold", "offset": 0, "length": 4},
        {"type": "code", "offset": 9, "length": 4},
    ]


def test_render_markdown_code_fence_language_is_string() -> None:
    text, entities = render_markdown("```py\nprint('x')\n```")

    assert text == "print('x')\n\n"
    assert entities is not None
    assert any(e.get("type") == "pre" and e.get("language") == "py" for e in entities)
    assert any(e.get("type") == "code" for e in entities)


def test_render_markdown_drops_local_text_links() -> None:
    text, entities = render_markdown("[/tmp/file.py#L12](/tmp/file.py#L12)")

    assert "/tmp/file.py#L12" in text
    assert not any(e.get("type") == "text_link" for e in entities)


def test_render_markdown_keeps_https_text_links() -> None:
    _, entities = render_markdown("[docs](https://example.com/path)")

    assert any(
        e.get("type") == "text_link" and e.get("url") == "https://example.com/path"
        for e in entities
    )


def test_render_markdown_tightens_loose_ordered_list_first_paragraph() -> None:
    text, entities = render_markdown(
        "1. **First item** body\n\n"
        "2. **Second item** body with `code`\n\n"
        "3. **Third item** body\n"
    )

    assert text == (
        "1. First item body\n2. Second item body with code\n3. Third item body\n"
    )
    assert entities == [
        {"type": "bold", "offset": 3, "length": 10},
        {"type": "bold", "offset": 22, "length": 11},
        {"type": "code", "offset": 44, "length": 4},
        {"type": "bold", "offset": 52, "length": 10},
    ]


def test_render_markdown_tightens_loose_unordered_list_first_paragraph() -> None:
    text, entities = render_markdown(
        "- **First item** body\n\n- **Second item** body\n"
    )

    assert text == "- First item body\n- Second item body\n"
    assert entities == [
        {"type": "bold", "offset": 2, "length": 10},
        {"type": "bold", "offset": 20, "length": 11},
    ]


def test_render_markdown_keeps_later_loose_list_paragraphs() -> None:
    text, _ = render_markdown(
        "1. **First item** body\n\n   additional paragraph\n\n2. **Second item** body\n"
    )

    assert text.startswith("1. First item body\n\n")
    assert "1.\n\n" not in text
    assert "2.\n\n" not in text
    assert "\xa0additional paragraph" in text


def test_render_markdown_keeps_ordered_numbering_with_unindented_sub_bullets() -> None:
    md = (
        "1. Tune maker\n"
        "- Sweep\n"
        "- Keep data\n"
        "1. Increase\n"
        "- Raise target\n"
        "- Keep\n"
        "1. Train\n"
        "- Start\n"
        "1. Add\n"
        "- Keep exposure\n"
        "1. Run\n"
        "- Target pnl\n"
    )

    text, _ = render_markdown(md)
    numbered = [line for line in text.splitlines() if re.match(r"^\d+\.\s", line)]

    assert numbered == [
        "1. Tune maker",
        "2. Increase",
        "3. Train",
        "4. Add",
        "5. Run",
    ]


def test_split_markdown_body_closes_and_reopens_fence() -> None:
    body = "```py\n" + ("line\n" * 10) + "```\n\npost"

    chunks = split_markdown_body(body, max_chars=40)

    assert len(chunks) > 1
    assert chunks[0].rstrip().endswith("```")
    assert chunks[1].startswith("```py\n")
