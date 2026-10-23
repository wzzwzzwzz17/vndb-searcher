# -*- coding: utf-8 -*-
"""网页详情专用：在 GGBase 搜索页按栏目标题相似度取前 3 个栏目页。

只返回 https://www.ggbases.com/view.so?id= 这种栏目地址。
不读取、不返回磁力、种子或压缩包地址。
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from urllib.parse import urlencode

import requests

SEARCH_URL = "https://www.ggbases.com/search.so"
VIEW_URL = "https://www.ggbases.com/view.so?id={id}"
_VIEW_HREF = re.compile(r'href="([^"]*view\.so\?id=(\d+))"[^>]*>(.*?)</a>', re.I | re.S)
_TAGS = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")
_NOISE = re.compile(r"[^0-9a-z\u3040-\u30ff\u4e00-\u9fff]+")
_FILE_EXT = re.compile(r"\.(?:zip|rar|7z|iso|mds|torrent)$", re.I)


def _clean_title(raw: str) -> str:
    text = _TAGS.sub("", raw or "")
    text = text.replace("\xa0", " ")
    return _SPACE.sub(" ", text).strip()


def _norm(text: str) -> str:
    title = _clean_title(text).lower()
    title = _FILE_EXT.sub("", title)
    return _NOISE.sub("", title)


def _similarity(query: str, title: str) -> float:
    left = _norm(query)
    right = _norm(title)
    if len(left) < 2 or len(right) < 2:
        return 0.0
    if left == right:
        return 1.0
    if left in right:
        return round(0.82 + 0.16 * (len(left) / len(right)), 4)
    if right in left and len(right) >= 4:
        return round(0.7 * (len(right) / len(left)), 4)
    return round(SequenceMatcher(None, left, right).ratio(), 4)


def _search_once(keyword: str) -> list[dict[str, str]]:
    response = requests.get(
        SEARCH_URL,
        params={"p": "0", "title": keyword, "advanced": "0"},
        headers={
            "User-Agent": "VNDB-Searcher/1.0 (local web)",
            "Accept": "text/html",
        },
        timeout=8,
    )
    response.raise_for_status()
    response.encoding = "utf-8"
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    for match in _VIEW_HREF.finditer(response.text):
        column_id = match.group(2)
        if column_id in seen:
            continue
        href = match.group(1).lower()
        if "magnet" in href or "down.so" in href:
            continue
        name = _clean_title(match.group(3))
        if not name:
            continue
        seen.add(column_id)
        found.append({"id": column_id, "name": name})
    return found


def search_ggbase(original: str, title: str) -> dict:
    """用原名和中文名各查第一页，按标题相似度返回最多 3 个栏目页。"""
    queries: list[str] = []
    for text in (original, title):
        cleaned = _SPACE.sub(" ", (text or "").strip())
        if len(_norm(cleaned)) >= 2 and cleaned not in queries:
            queries.append(cleaned)
    if not queries:
        return {"ok": False, "reason": "没有可用于 GGBase 的作品名", "pages": []}
    rows: dict[str, dict] = {}
    try:
        for keyword in queries[:2]:
            for item in _search_once(keyword):
                score = max(_similarity(query, item["name"]) for query in queries)
                current = rows.get(item["id"])
                if current is None or score > current["score"]:
                    rows[item["id"]] = {
                        "name": item["name"],
                        "page": VIEW_URL.format(id=item["id"]),
                        "score": score,
                    }
    except requests.RequestException:
        return {"ok": False, "reason": "GGBase 暂时连不上", "pages": []}
    ranked = [row for row in rows.values() if row["score"] >= 0.55]
    ranked.sort(key=lambda row: (-row["score"], len(row["name"])))
    pages = []
    for row in ranked[:3]:
        if not re.fullmatch(r"https://www\.ggbases\.com/view\.so\?id=\d+", row["page"]):
            continue
        pages.append({"name": row["name"], "page": row["page"]})
    if not pages:
        return {"ok": False, "reason": "GGBase 第一页没有足够相似的栏目", "pages": []}
    return {
        "ok": True,
        "name": pages[0]["name"],
        "page": pages[0]["page"],
        "pages": pages,
        "intro": "",
        "tags": [],
    }
