from __future__ import annotations
from .base import Tool

def _web_fetch(url: str, max_tokens: int = 2000) -> str:
    import httpx
    from markdownify import markdownify as md
    from agent.context import truncate_observation
    resp = httpx.get(url, timeout=20, follow_redirects=True)
    resp.raise_for_status()
    text = md(resp.text)                     # HTML -> markdown
    return truncate_observation(text, max_chars=max_tokens * 4)


web_fetch_tool = Tool("web_fetch", "抓取 URL 并转为 markdown（受 token 预算限制）。",
                      {"type": "object", "properties": {"url": {"type": "string"}},
                       "required": ["url"]}, _web_fetch)