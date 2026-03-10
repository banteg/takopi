import re

import pytest

from takopi.telegram.render import _BARE_URL_RE, render_markdown, split_markdown_body

URL_RECOGNITION_CASES = [
    "http://example.com",
    "https://example.com",
    "http://www.example.com",
    "https://www.example.com/",
    "https://example.com/path",
    "https://example.com/path/",
    "https://example.com/path/to/resource",
    "https://example.com/path/to/resource/",
    "https://example.com/path/to/resource.html",
    "https://example.com/path/to/resource.html?query=1",
    "https://example.com/path/to/resource.html?query=1&foo=bar",
    "https://example.com/path/to/resource?query=слово",
    "https://example.com/path/to/resource?query=слово#anchor",
    "https://example.com/#anchor",
    "https://example.com/path#section-1",
    "https://sub.example.com",
    "https://deep.sub.domain.example.com",
    "http://localhost",
    "http://localhost:3000",
    "http://127.0.0.1",
    "http://127.0.0.1:8080/health",
    "https://[2001:db8::1]/",
    "https://[2001:db8::1]:8443/status",
    "ftp://ftp.example.com/pub/file.txt",
    "ftps://secure.example.com/downloads",
    "sftp://user@example.com/home/user/file",
    "mailto:user@example.com",
    "mailto:user.name+tag@example.co.uk",
    "tel:+1-202-555-0100",
    "tel:+7-999-123-45-67",
    "ssh://user@example.com",
    "ssh://user@example.com:2222/home/user",
    "file:///etc/hosts",
    "file:///C:/Windows/System32/drivers/etc/hosts",
    "data:text/plain,Hello%20World",
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAA...",
    "https://example.com?only=query",
    "https://example.com?param1=value1&param2=&param3",
    "https://example.com?encoded=%D1%82%D0%B5%D1%81%D1%82",
    "https://example.com?arr[]=1&arr[]=2&arr[]=3",
    "https://example.com/path%20with%20spaces",
    "https://example.com/путь/с/юникодом",
    "https://пример.рф",
    "https://домен.рф/путь?параметр=значение",
    "http://example.com:80",
    "https://example.com:443",
    "https://example.com:8443/custom-port",
    "http://blog.example.com/article?id=123",
    "https://shop.example.com/products/12345?color=red&size=m",
    "https://api.example.com/v1/users/42",
    "https://api.example.com/v1/users/42?include=posts,comments",
    "https://cdn.example.com/assets/app.js?v=1.2.3",
    "https://example.com/path?redirect=https%3A%2F%2Fother.com%2Fpage",
    "https://example.com/path;param1;param2?query=1",
    "https://example.com/path/to/resource.json",
    "https://example.com/path/to/resource.xml",
    "https://example.com/path/to/resource.jpeg",
    "https://example.com/path/to/resource.tar.gz",
    "https://example.com/.well-known/security.txt",
    "https://example.com/.gitignore",
    "https://user:pass@example.com",
    "https://user:pass@example.com:8443/private",
    "http://user@example.com",
    "http://user:@example.com",
    "https://example.com/path?empty=",
    "https://example.com/path?flag",
    "https://example.com/#",
    "https://example.com/#/spa/route",
    "https://example.com/path/?trailing=slash",
    "https://example.com/a/b/../c/d",
    "https://example.com/a/%2E%2E/c/d",
    "https://example.com/a/b/c?x=1#y=2",
    "https://example.com:12345/custom/port/path",
    "https://sub-domain.example.co.uk/path",
    "https://sub_domain.example.com/path_with_underscores",
    "https://example.com/path-with-dashes",
    "https://example.com/123/456/789",
    "https://example.com/2024/12/31/happy-new-year",
    "https://example.com/?q=URL+parsing+test",
    "https://example.com/?q=100%25+coverage",
    "https://example.com/?q=%E2%9C%93",
    "https://例子.测试/路径?查询=值",
    "https://example.com/(test)/[brackets]",
    "https://example.com/path?special=!@#$%^&*()",
    "https://example.com/path?json=%7B%22a%22%3A1%2C%22b%22%3A2%7D",
    'https://example.com/path,\'"quotes"',
    "https://example.com/path?with=comma,semicolon;colon:",
    "https://example.com/path?multi=line%0Avalue",
    "https://example.com/path?tab=one%09two",
    "https://user.name+tag@example.com/profile",
    "https://example.travel",
    "https://example.museum",
    "https://example.xyz",
    "https://example.dev",
    "https://example.ai",
    "https://sub.пример.рф/каталог/товар?id=10&sort=asc",
    "https://example.com:65535/max-port",
    "http://example.com:1/min-port",
    "https://example.com/?emoji=%F0%9F%98%80",
    "https://example.com/very/long/path/with/many/segments/and/query?one=1&two=2&three=3#long-fragment-section-10",
]


def test_render_markdown_basic_entities() -> None:
    text, entities = render_markdown("**bold** and `code`")

    assert text == "bold and code\n\n"
    assert entities == [
        {"type": "bold", "offset": 0, "length": 4},
        {"type": "code", "offset": 9, "length": 4},
    ]


@pytest.mark.parametrize("url", URL_RECOGNITION_CASES)
def test_bare_url_regex_recognizes_url_corpus(url: str) -> None:
    match = _BARE_URL_RE.fullmatch(url)
    assert match is not None


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


def test_render_markdown_linkifies_bare_https_urls() -> None:
    text, entities = render_markdown("See https://example.com/path for docs.")

    assert "https://example.com/path" in text
    assert any(
        e.get("type") == "text_link" and e.get("url") == "https://example.com/path"
        for e in entities
    )


def test_render_markdown_linkifies_bare_www_urls() -> None:
    text, entities = render_markdown("See www.example.com/path for docs")

    assert "www.example.com/path" in text
    assert any(
        e.get("type") == "text_link" and e.get("url") == "www.example.com/path"
        for e in entities
    )


def test_render_markdown_linkifies_bare_urls() -> None:
    text, entities = render_markdown("See example.com/path for docs.")

    assert "example.com/path" in text
    assert any(
        e.get("type") == "text_link" and e.get("url") == "example.com/path"
        for e in entities
    )


def test_render_markdown_does_not_linkify_urls_inside_inline_code() -> None:
    _, entities = render_markdown("Use `https://example.com/path` literally.")

    assert not any(
        e.get("type") == "text_link" and e.get("url") == "https://example.com/path"
        for e in entities
    )


def test_render_markdown_does_not_linkify_urls_inside_fenced_code() -> None:
    _, entities = render_markdown("```txt\nhttps://example.com/path\n```")

    assert not any(
        e.get("type") == "text_link" and e.get("url") == "https://example.com/path"
        for e in entities
    )


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
