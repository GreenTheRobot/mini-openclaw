from __future__ import annotations
from .base import Tool


def _web_search(query: str, max_results: int = 5) -> str:
    import re
    from html import unescape
    from urllib.parse import parse_qs, quote_plus, unquote, urlparse

    import httpx

    url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    resp = httpx.get(
        url,
        timeout=20,
        follow_redirects=True,
        headers={"User-Agent": "mini-openclaw/0.1"},
    )
    resp.raise_for_status()

    results: list[str] = []
    for m in re.finditer(
        r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
        resp.text,
        flags=re.S,
    ):
        href = unescape(m.group(1))
        title = re.sub(r"<.*?>", "", m.group(2), flags=re.S)
        title = unescape(re.sub(r"\s+", " ", title)).strip()
        parsed = urlparse(href)
        if parsed.path.startswith("/l/"):
            href = unquote(parse_qs(parsed.query).get("uddg", [href])[0])
        results.append(f"- {title}\n  {href}")
        if len(results) >= max_results:
            break

    return "\n".join(results) if results else f"[无搜索结果] query={query}"


def _web_fetch(url: str, max_tokens: int = 2000) -> str:
    import warnings

    import httpx
    from bs4 import XMLParsedAsHTMLWarning
    from markdownify import markdownify as md
    from agent.context import truncate_observation

    resp = httpx.get(url, timeout=20, follow_redirects=True)
    resp.raise_for_status()
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
        text = md(resp.text)                 # HTML -> markdown
    return truncate_observation(text, max_chars=max_tokens * 4)


web_search_tool = Tool("web_search", "搜索网页并返回候选结果标题和 URL。",
                       {"type": "object",
                        "properties": {"query": {"type": "string"},
                                       "max_results": {"type": "integer"}},
                        "required": ["query"]}, _web_search)

web_fetch_tool = Tool("web_fetch", "抓取 URL 并转为 markdown（受 token 预算限制）。",
                      {"type": "object", "properties": {"url": {"type": "string"}},
                       "required": ["url"]}, _web_fetch)
