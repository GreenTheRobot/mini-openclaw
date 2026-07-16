#!/usr/bin/env python3
"""
从 Research Agent 输出的论文列表中提取 arXiv ID，
通过 arXiv API 获取元数据，生成结构化 JSON 文件。

输出: recent_papers.json
"""

import json
import os
import re
import time
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET

# ---------- 配置 ----------
OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_PATH = os.path.join(OUTPUT_DIR, "recent_papers.json")

# Research Agent 实际输出的论文 arXiv ID 列表（从 step-01-arxiv_search.txt 中提取）
ARXIV_IDS = [
    "2607.11434",  # Direct Image-to-Modern Vietnamese Translation via Multimodal RLHF
    "2607.11131",  # TIGER: Text-Conditioned Visual Gated Routing for Multimodal Speculative Decoding
    "2607.10640",  # Spectral Heat Flow for Conservative Token Condensation
    "2607.10120",  # WeaveEarth: A Unified Large-Scale Benchmark for Remote Sensing
    "2607.09080",  # GeoTrace: A Benchmark for Multimodal Geo-Aware Tracing
    "2607.08221",  # LUMI: Leveraging Language to Understand Multimodal Instructions
    "2607.07033",  # AnchorPrune: Relevance-Anchored Contextual Expansion for Visual Token Pruning
]

# arXiv API 端点
ARXIV_API_BASE = "http://export.arxiv.org/api/query"

# 压缩方法关键词（用于从摘要中匹配）
COMPRESSION_KEYWORDS = [
    "pruning", "quantization", "distillation", "knowledge distillation",
    "token pruning", "visual token pruning", "model compression",
    "efficient", "lightweight", "sparse", "speculative decoding",
    "tokenizer", "autoencoder", "sparse autoencoder",
    "low-rank", "deployment efficiency", "inference efficiency",
    "parameter efficient", "sub-billion", "small model",
]


def fetch_arxiv_metadata(arxiv_id):
    """
    通过 arXiv API 获取单篇论文的元数据。
    返回解析后的 dict，或 None（失败时）。
    """
    # 标准化 ID：去掉版本号
    base_id = re.sub(r"v\d+$", "", arxiv_id.strip())
    query_id = base_id

    url = f"{ARXIV_API_BASE}?id_list={query_id}&max_results=1"
    print(f"  Fetching: {url}")

    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "MiniOpenClaw/1.0 (mailto:research@example.com)"
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            xml_data = resp.read().decode("utf-8")
    except Exception as e:
        print(f"  ERROR fetching {arxiv_id}: {e}")
        return None

    # 解析 XML
    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "arxiv": "http://arxiv.org/schemas/atom",
    }

    try:
        root = ET.fromstring(xml_data)
    except ET.ParseError as e:
        print(f"  ERROR parsing XML for {arxiv_id}: {e}")
        return None

    entries = root.findall("atom:entry", ns)
    if not entries:
        print(f"  WARNING: No entry found for {arxiv_id}")
        return None

    entry = entries[0]

    # 标题
    title_el = entry.find("atom:title", ns)
    title = title_el.text.strip().replace("\n", " ") if title_el is not None else ""

    # 摘要
    summary_el = entry.find("atom:summary", ns)
    abstract = summary_el.text.strip().replace("\n", " ") if summary_el is not None else ""

    # 作者
    authors = []
    for author_el in entry.findall("atom:author", ns):
        name_el = author_el.find("atom:name", ns)
        if name_el is not None:
            authors.append(name_el.text.strip())

    # 发布日期（取 published 字段）
    published_el = entry.find("atom:published", ns)
    date = published_el.text.strip()[:10] if published_el is not None else ""

    # arXiv ID（从 id URL 中提取）
    id_el = entry.find("atom:id", ns)
    full_id = ""
    if id_el is not None:
        id_url = id_el.text.strip()
        # 从 URL 中提取 ID，如 http://arxiv.org/abs/2607.11434v1
        m = re.search(r"abs/([\d.]+(?:v\d+)?)", id_url)
        if m:
            full_id = m.group(1)

    # PDF URL
    pdf_url = f"https://arxiv.org/pdf/{full_id}" if full_id else ""

    # 从摘要中匹配压缩方法
    abstract_lower = abstract.lower()
    compression_methods = []
    for kw in COMPRESSION_KEYWORDS:
        if kw.lower() in abstract_lower:
            compression_methods.append(kw)

    return {
        "title": title,
        "authors": authors,
        "date": date,
        "arxiv_id": full_id,
        "abstract": abstract,
        "compression_methods": compression_methods,
        "pdf_url": pdf_url,
    }


def main():
    print(f"Starting metadata fetch for {len(ARXIV_IDS)} papers...")
    print(f"Output: {OUTPUT_PATH}")
    print()

    results = []
    errors = []

    for i, arxiv_id in enumerate(ARXIV_IDS):
        print(f"[{i+1}/{len(ARXIV_IDS)}] Processing {arxiv_id}...")
        meta = fetch_arxiv_metadata(arxiv_id)
        if meta:
            results.append(meta)
            print(f"  OK: {meta['title'][:80]}...")
        else:
            errors.append(arxiv_id)
            print(f"  FAILED: {arxiv_id}")

        # arXiv API 限流：每 3 秒最多 1 个请求
        if i < len(ARXIV_IDS) - 1:
            time.sleep(3)

    print()
    print(f"Successfully fetched: {len(results)}/{len(ARXIV_IDS)}")
    if errors:
        print(f"Failed: {errors}")

    # 写入 JSON
    output_data = {
        "query_info": {
            "topic": "multimodal_model_compression",
            "search_date": "2026-07-16",
            "date_range": "2026-07-09 to 2026-07-16",
            "total_papers": len(results),
        },
        "papers": results,
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)

    print(f"\nJSON written to: {OUTPUT_PATH}")
    print(f"File size: {os.path.getsize(OUTPUT_PATH)} bytes")


if __name__ == "__main__":
    main()
