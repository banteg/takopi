from takopi.telegram.rich_message import (
    build_input_rich_message,
    markdown_has_gfm_table,
    should_use_rich_message,
)


def test_markdown_has_gfm_table() -> None:
    assert markdown_has_gfm_table(
        "| A | B |\n|---|---|\n| 1 | 2 |\n",
    )
    assert not markdown_has_gfm_table("no tables here")


def test_should_use_rich_auto() -> None:
    md = "| h |\n|---|\n| x |\n"
    assert should_use_rich_message(md, "auto")
    assert not should_use_rich_message(md, "off")
    assert should_use_rich_message("hello", "always")


def test_build_input_rich_message() -> None:
    payload = build_input_rich_message("# hi\n")
    assert payload["markdown"] == "# hi\n"
    assert payload["skip_entity_detection"] is False
