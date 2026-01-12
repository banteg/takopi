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


def test_split_markdown_body_closes_and_reopens_fence() -> None:
    body = "```py\n" + ("line\n" * 10) + "```\n\npost"

    chunks = split_markdown_body(body, max_chars=40)

    assert len(chunks) > 1
    assert chunks[0].rstrip().endswith("```")
    assert chunks[1].startswith("```py\n")
