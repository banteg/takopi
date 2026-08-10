from takopi.telegram.rich_message import (
    MAX_RICH_CHARS,
    build_input_rich_message,
    escape_raw_html,
    markdown_has_gfm_table,
    rich_limit_exceeded,
    should_use_rich_message,
)

TABLE = "| A | B |\n|---|---|\n| 1 | 2 |\n"


def test_markdown_has_gfm_table() -> None:
    assert markdown_has_gfm_table(TABLE)
    assert not markdown_has_gfm_table("no tables here")


def test_markdown_ignores_table_inside_code_fence() -> None:
    fenced = f"here is a sample:\n\n```\n{TABLE}```\n"
    assert not markdown_has_gfm_table(fenced)


def test_should_use_rich_auto() -> None:
    assert should_use_rich_message(TABLE, "auto")
    assert not should_use_rich_message(TABLE, "off")
    assert should_use_rich_message("hello", "always")
    assert not should_use_rich_message("   ", "always")


def test_should_use_rich_auto_headings_need_line_start() -> None:
    filler = "word " * 120
    assert should_use_rich_message(f"## Results\n\n{filler}", "auto")
    # a heading-looking string mid-line is not a heading
    assert not should_use_rich_message(f"run `git log ## foo`\n\n{filler}", "auto")
    # nor is one inside a fenced block
    assert not should_use_rich_message(
        f"```sh\n## not a heading\n```\n\n{filler}", "auto"
    )
    # short answers stay on the regular path
    assert not should_use_rich_message("## Results\n\nshort", "auto")


def test_rich_limit_exceeded() -> None:
    assert rich_limit_exceeded(TABLE) is None
    wide = "|" + "|".join(f" c{i} " for i in range(25)) + "|"
    assert rich_limit_exceeded(f"{wide}\n") == "columns"
    assert rich_limit_exceeded("x" * (MAX_RICH_CHARS + 1)) == "chars"
    assert rich_limit_exceeded("\n\n".join(f"para {i}" for i in range(600))) == "blocks"


def test_escape_raw_html_leaves_code_alone() -> None:
    md = "use `Vec<T>` here\n\n```rust\nlet x: Vec<T> = vec![];\n```\n\n<b>raw</b>\n"
    escaped = escape_raw_html(md)

    assert "`Vec<T>`" in escaped
    assert "let x: Vec<T> = vec![];" in escaped
    assert "&lt;b>raw&lt;/b>" in escaped


def test_escape_raw_html_keeps_blockquotes() -> None:
    assert escape_raw_html("> quoted\n") == "> quoted\n"


def test_build_input_rich_message_escapes() -> None:
    payload = build_input_rich_message("# hi <T>\n")
    assert payload == {"markdown": "# hi &lt;T>\n"}
