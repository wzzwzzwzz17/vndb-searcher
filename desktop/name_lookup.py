# -*- coding: utf-8 -*-
"""根据大致作品名，结合必应检索和片段搜索，定位具体游戏。"""

from __future__ import annotations

import json
import re
import time
from typing import Any

import requests
from openai import OpenAI

import config
from community_scores import _shinnku_pages, attach_community_scores
from vndb_api import VndbClient, filter_items_japanese

_BING_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,ja;q=0.7",
}


def _bing(query: str) -> list[dict[str, str]]:
    response = requests.get(
        "https://www.bing.com/search",
        params={"q": query, "count": "8", "setlang": "zh-Hans"},
        headers=_BING_HEADERS,
        timeout=12,
    )
    response.raise_for_status()
    html = response.text
    titles = re.findall(r"<h2[^>]*>\s*<a[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>", html, re.I | re.S)
    snippets = re.findall(r"<p[^>]*>(.*?)</p>", html, re.I | re.S)
    clean_snippets = [re.sub(r"<[^>]+>", "", item) for item in snippets]
    rows = []
    for index, (url, title) in enumerate(titles[:6]):
        if "bing.com" in url:
            continue
        rows.append(
            {
                "title": re.sub(r"<[^>]+>", "", title),
                "url": url,
                "snippet": clean_snippets[index] if index < len(clean_snippets) else "",
            }
        )
    return rows


def _analyze(user_name: str, snippets: list[dict[str, str]]) -> dict[str, Any]:
    key = config.DEEPSEEK_API_KEY
    if not key or key.startswith("在此填入"):
        raise ValueError("请先配置 DeepSeek API Key")
    client = OpenAI(api_key=key, base_url=config.DEEPSEEK_BASE_URL)
    response = client.chat.completions.create(
        model=config.DEEPSEEK_MODEL,
        temperature=0.1,
        max_tokens=500,
        messages=[
            {
                "role": "system",
                "content": (
                    "你根据用户给出的大致作品名和必应搜索摘要，判断最可能的那一部视觉小说。"
                    "只输出 JSON：{\"zh\":\"\",\"en\":\"\",\"ja\":\"\",\"vndb_id\":\"v123或空字符串\",\"aliases\":[]}"
                    "。不要编造没有依据的 vndb_id。不要输出下载链接。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps({"name": user_name, "bing": snippets}, ensure_ascii=False),
            },
        ],
    )
    text = response.choices[0].message.content or ""
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return {"zh": user_name, "en": "", "ja": "", "vndb_id": "", "aliases": []}
    data = json.loads(match.group(0))
    return data if isinstance(data, dict) else {"zh": user_name, "en": "", "ja": "", "vndb_id": "", "aliases": []}


def _vndb_hits(analysis: dict[str, Any], user_name: str) -> list[dict[str, Any]]:
    client = VndbClient()
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    vndb_id = str(analysis.get("vndb_id") or "")
    if re.fullmatch(r"v\d+", vndb_id):
        for item in client.search_vn({"filters": ["id", "=", vndb_id], "limit": 1}, use_cache=False):
            if item.get("id") and item["id"] not in seen:
                seen.add(item["id"])
                found.append(item)
    for name in (analysis.get("ja"), analysis.get("en"), analysis.get("zh"), user_name):
        text = str(name or "").strip()
        if len(text) < 2:
            continue
        try:
            hits = client.search_vn(
                {
                    "filters": ["and", ["search", "=", text], ["olang", "=", "ja"]],
                    "sort": "searchrank",
                    "reverse": True,
                    "limit": 5,
                },
                use_cache=False,
            )
        except Exception:
            continue
        for item in hits:
            if item.get("id") and item["id"] not in seen:
                seen.add(item["id"])
                found.append(item)
        if len(found) >= 10:
            break
    return filter_items_japanese(found)[:10]


def lookup_name(user_name: str, must_find: bool = False) -> dict[str, Any]:
    started = time.perf_counter()
    name = (user_name or "").strip()
    if len(name) < 1:
        raise ValueError("请输入作品名")
    bing_ok = True
    snippets: list[dict[str, str]] = []
    try:
        snippets = _bing(f"{name} visual novel vndb")
        if len(snippets) < 2:
            snippets += _bing(f"site:vndb.org {name}")
    except requests.RequestException:
        bing_ok = False
    analysis = _analyze(name, snippets[:8])
    items = _vndb_hits(analysis, name)
    if items:
        attach_community_scores(items)
    queries = [str(analysis.get("ja") or ""), str(analysis.get("en") or ""), str(analysis.get("zh") or ""), name]
    shinnku = _shinnku_pages([text for text in queries if text.strip()], must_find)
    elapsed = round(time.perf_counter() - started, 1)
    return {
        "elapsed": elapsed,
        "bing_ok": bing_ok,
        "analysis": {
            "zh": analysis.get("zh") or "",
            "en": analysis.get("en") or "",
            "ja": analysis.get("ja") or "",
            "vndb_id": analysis.get("vndb_id") or "",
        },
        "items": items,
        "shinnku": shinnku,
    }
