from takopi.render import render_markdown


def test_render_markdown_basic_entities(snapshot) -> None:
    text, entities = render_markdown("**bold** and `code`")

    assert snapshot == (text, entities)


def test_render_markdown_code_fence_language_is_string(snapshot) -> None:
    text, entities = render_markdown("```py\nprint('x')\n```")

    assert snapshot == (text, entities)
