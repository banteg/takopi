from takopi.telegram.render import render_markdown


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


def test_render_markdown_tightens_numbered_lists() -> None:
    text, _ = render_markdown(
        "Observations\n"
        "1.\n\n"
        "  Clean implementation - The flow is straightforward\n\n"
        "2.\n\n"
        "  Good error handling - Each failure point is covered\n"
    )

    assert "1. Clean implementation - The flow is straightforward" in text
    assert "2. Good error handling - Each failure point is covered" in text
    assert "\n1.\n\n" not in text
    assert "\xa0" not in text
