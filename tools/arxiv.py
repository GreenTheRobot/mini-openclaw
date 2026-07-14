"""Structured arXiv search with optional submitted-date filtering."""
from __future__ import annotations

import re
from datetime import date
from xml.etree import ElementTree

import httpx

from .base import Tool, ToolResult


ATOM = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


def _normalize_date(value: str | None, field: str) -> str | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value).strftime("%Y%m%d")
    except ValueError as exc:
        raise ValueError(f"{field} 必须使用 YYYY-MM-DD 格式：{value}") from exc


def _plain_query(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("query 不能为空")
    if re.search(r"(?:^|\s)(?:all|ti|abs|au|cat):", value):
        return value
    terms = re.findall(r"[A-Za-z0-9_.+-]+|[\u4e00-\u9fff]+", value)
    if not terms:
        raise ValueError("query 没有可检索关键词")
    return " AND ".join(f'all:"{term}"' for term in terms[:12])


def _text(node: ElementTree.Element | None) -> str:
    if node is None or not node.text:
        return ""
    return re.sub(r"\s+", " ", node.text).strip()


def _arxiv_search(
    query: str,
    start_date: str | None = None,
    end_date: str | None = None,
    category: str | None = None,
    max_results: int = 10,
    *,
    _transport=None,
) -> ToolResult:
    """Return paper metadata and abstracts from the arXiv Atom API."""
    try:
        start = _normalize_date(start_date, "start_date")
        end = _normalize_date(end_date, "end_date")
        if start and end and start > end:
            raise ValueError("start_date 不能晚于 end_date")
        limit = max(1, min(int(max_results), 30))
        clauses = [_plain_query(query)]
        if category:
            cleaned = category.strip()
            if not re.fullmatch(r"[A-Za-z.-]+", cleaned):
                raise ValueError(f"无效 arXiv 分类：{category}")
            clauses.append(f"cat:{cleaned}")
        if start or end:
            lower = (start or "19910101") + "0000"
            upper = (end or "29991231") + "2359"
            clauses.append(f"submittedDate:[{lower} TO {upper}]")

        params = {
            "search_query": " AND ".join(clauses),
            "start": 0,
            "max_results": limit,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
        with httpx.Client(transport=_transport, timeout=30, follow_redirects=True, trust_env=False) as client:
            response = client.get(
                "https://export.arxiv.org/api/query",
                params=params,
                headers={"User-Agent": "mini-openclaw/0.1 (academic research assistant)"},
            )
        response.raise_for_status()
        root = ElementTree.fromstring(response.content)
        entries = root.findall("atom:entry", ATOM)
        if not entries:
            window = f"，日期 {start_date or '不限'} 至 {end_date or '不限'}"
            return ToolResult(f"[arXiv 无匹配论文] query={query}{window}", True, "ok")

        blocks: list[str] = []
        for index, entry in enumerate(entries, 1):
            identifier = _text(entry.find("atom:id", ATOM))
            arxiv_id = identifier.rstrip("/").split("/")[-1]
            title = _text(entry.find("atom:title", ATOM))
            summary = _text(entry.find("atom:summary", ATOM))
            published = _text(entry.find("atom:published", ATOM))[:10]
            updated = _text(entry.find("atom:updated", ATOM))[:10]
            authors = [
                _text(author.find("atom:name", ATOM))
                for author in entry.findall("atom:author", ATOM)
            ]
            categories = [node.attrib.get("term", "") for node in entry.findall("atom:category", ATOM)]
            primary = entry.find("arxiv:primary_category", ATOM)
            primary_category = primary.attrib.get("term", "") if primary is not None else ""
            abs_url = next(
                (link.attrib.get("href", "") for link in entry.findall("atom:link", ATOM)
                 if link.attrib.get("rel") == "alternate"),
                f"https://arxiv.org/abs/{arxiv_id}",
            )
            blocks.append(
                f"## {index}. {title}\n"
                f"- arXiv：{arxiv_id}\n"
                f"- 首次提交：{published}\n"
                f"- 最近更新：{updated}\n"
                f"- 作者：{', '.join(authors)}\n"
                f"- 主分类：{primary_category or '未标注'}\n"
                f"- 全部分类：{', '.join(categories)}\n"
                f"- 摘要：{summary}\n"
                f"- 来源：{abs_url}"
            )
        return ToolResult("\n\n".join(blocks), True, "ok")
    except ValueError as exc:
        return ToolResult(str(exc), False, "invalid_arguments")
    except ElementTree.ParseError as exc:
        return ToolResult(f"arXiv 返回了无法解析的 XML：{exc}", False, "invalid_response")
    except httpx.TimeoutException as exc:
        return ToolResult(f"arXiv 检索超时：{exc}", False, "network_timeout")
    except httpx.HTTPStatusError as exc:
        return ToolResult(
            f"arXiv API 返回 HTTP {exc.response.status_code}：{exc.request.url}",
            False,
            "http_client_error" if exc.response.status_code < 500 else "http_server_error",
        )
    except httpx.HTTPError as exc:
        return ToolResult(f"arXiv 检索失败：{exc}", False, "network_error")


arxiv_search_tool = Tool(
    "arxiv_search",
    "按关键词、arXiv 分类和提交日期区间检索论文，返回标题、作者、日期、分类、摘要与来源链接。近期文献检索应优先使用此工具。",
    {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "start_date": {"type": "string"},
            "end_date": {"type": "string"},
            "category": {"type": "string"},
            "max_results": {"type": "integer"},
        },
        "required": ["query"],
    },
    _arxiv_search,
)