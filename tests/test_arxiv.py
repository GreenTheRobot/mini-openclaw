import httpx

from tools.arxiv import _arxiv_search
from tools.base import build_default_registry


ATOM_RESPONSE = b'''<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>https://arxiv.org/abs/2607.12345v1</id>
    <updated>2026-07-13T12:00:00Z</updated>
    <published>2026-07-13T12:00:00Z</published>
    <title>Efficient Multimodal Compression</title>
    <summary>We compress visual tokens while preserving reasoning accuracy.</summary>
    <author><name>Alice Zhang</name></author>
    <author><name>Bob Li</name></author>
    <category term="cs.CV" />
    <category term="cs.CL" />
    <arxiv:primary_category term="cs.CV" />
    <link href="https://arxiv.org/abs/2607.12345v1" rel="alternate" type="text/html" />
  </entry>
</feed>'''


def test_arxiv_search_returns_structured_metadata_and_date_filter():
    seen = {}

    def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        return httpx.Response(200, content=ATOM_RESPONSE, request=request)

    result = _arxiv_search(
        "multimodal compression",
        start_date="2026-07-07",
        end_date="2026-07-14",
        category="cs.CV",
        max_results=5,
        _transport=httpx.MockTransport(handler),
    )

    assert result.success is True
    assert "Efficient Multimodal Compression" in result.content
    assert "首次提交：2026-07-13" in result.content
    assert "Alice Zhang, Bob Li" in result.content
    assert "摘要：We compress visual tokens" in result.content
    assert "https://arxiv.org/abs/2607.12345v1" in result.content
    assert "submittedDate" in seen["url"]
    assert "cs.CV" in seen["url"]


def test_arxiv_search_rejects_invalid_date_without_network():
    result = _arxiv_search("compression", start_date="2026/07/07")
    assert result.success is False
    assert result.category == "invalid_arguments"
    assert "YYYY-MM-DD" in result.content


def test_arxiv_search_empty_feed_is_successful_zero_match():
    feed = b'<feed xmlns="http://www.w3.org/2005/Atom"></feed>'
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=feed, request=request))
    result = _arxiv_search("rare topic", _transport=transport)
    assert result.success is True
    assert "arXiv 无匹配论文" in result.content


def test_default_registry_contains_arxiv_search():
    assert "arxiv_search" in build_default_registry().names()