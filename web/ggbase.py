# -*- coding: utf-8 -*-
"""网页详情专用：GGBase 只按日文搜索栏目页。

搜索顺序与真红小站相同：全日文名，连续一半，再随机四分之一。
不读取、不返回磁力、种子或压缩包地址。
"""

from __future__ import annotations

import html
import random
import re
import time
from difflib import SequenceMatcher

import requests

SEARCH_URL = "https://www.ggbases.com/search.so"
VIEW_URL = "https://www.ggbases.com/view.so?id={id}"
_VIEW_HREF = re.compile(r'href="([^"]*view\.so\?id=(\d+))"[^>]*>(.*?)</a>', re.I | re.S)
_TAGS = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")
_KANA = re.compile(r"[\u3040-\u30ff]")
_CJK = re.compile(r"[\u4e00-\u9fff]")
_NOISE = re.compile(r"[^0-9a-z\u3040-\u30ff\u4e00-\u9fff]+")
_FILE_EXT = re.compile(r"\.(?:zip|rar|7z|iso|mds|torrent)$", re.I)


def _clean_title(raw: str) -> str:
    text = html.unescape(_TAGS.sub("", raw or ""))
    text = text.replace("\xa0", " ")
    return _SPACE.sub(" ", text).strip()


def _norm(text: str) -> str:
    title = _clean_title(text).lower()
    title = _FILE_EXT.sub("", title)
    return _NOISE.sub("", title)


def _japanese_source(original: str, title: str) -> str:
    """只取日文名。有假名时用假名更多的那条，不用中文译名或英文名。"""
    texts: list[str] = []
    for text in (original, title):
        cleaned = _SPACE.sub(" ", (text or "").strip())
        if cleaned and cleaned not in texts:
            texts.append(cleaned)
    kana = [text for text in texts if _KANA.search(text)]
    if kana:
        kana.sort(key=lambda text: len(_KANA.findall(text)), reverse=True)
        return kana[0]
    orig = _SPACE.sub(" ", (original or "").strip())
    shown = _SPACE.sub(" ", (title or "").strip())
    if orig and orig != shown and _CJK.search(orig):
        return orig
    return ""


def _add_term(terms: list[str], text: str) -> None:
    cleaned = _SPACE.sub(" ", (text or "").strip())
    if len(_norm(cleaned)) < 2 or cleaned in terms:
        return
    terms.append(cleaned)


def _japanese_terms(text: str, must_find: bool) -> list[str]:
    """全日文名，再取连续一半，最后多次随机取四分之一。"""
    terms: list[str] = []
    _add_term(terms, text)
    chars = list(text)
    count = len(chars)
    if count >= 4:
        half = max(2, count // 2)
        _add_term(terms, "".join(chars[:half]))
        middle = max(0, (count - half) // 2)
        _add_term(terms, "".join(chars[middle : middle + half]))
        _add_term(terms, "".join(chars[-half:]))
        quarter = max(2, count // 4)
        rounds = 8 if must_find else 4
        for _ in range(rounds):
            if count <= quarter:
                _add_term(terms, text)
                break
            start = random.randint(0, count - quarter)
            _add_term(terms, "".join(chars[start : start + quarter]))
    return terms[: 12 if must_find else 8]


def _similarity(query: str, title: str) -> float:
    left = _norm(query)
    right = _norm(title)
    if len(left) < 2 or len(right) < 2:
        return 0.0
    if left == right or left in right:
        return 1.0
    return round(SequenceMatcher(None, left, right).ratio(), 4)


def _half_texts(text: str) -> list[str]:
    chars = list(text)
    count = len(chars)
    if count < 4:
        return []
    half = max(2, count // 2)
    middle = max(0, (count - half) // 2)
    return [
        "".join(chars[:half]),
        "".join(chars[middle : middle + half]),
        "".join(chars[-half:]),
    ]


def _full_or_half(japanese: str, column_name: str) -> bool:
    right = _norm(column_name)
    left = _norm(japanese)
    if len(right) < 2 or len(left) < 2:
        return False
    if left in right or right in left:
        return True
    return any(len(_norm(piece)) >= 2 and _norm(piece) in right for piece in _half_texts(japanese))


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


def _as_pages(items: list[dict[str, str]]) -> list[dict[str, str]]:
    pages: list[dict[str, str]] = []
    for item in items:
        page = VIEW_URL.format(id=item["id"])
        if not re.fullmatch(r"https://www\.ggbases\.com/view\.so\?id=\d+", page):
            continue
        pages.append({"name": item["name"], "page": page})
    return pages


def _pack(pages: list[dict[str, str]], judge: bool) -> dict:
    return {
        "ok": True,
        "name": pages[0]["name"],
        "page": pages[0]["page"],
        "pages": pages,
        "judge": judge,
        "intro": "",
        "tags": [],
    }


def search_ggbase(original: str, title: str, must_find: bool = False) -> dict:
    """只用日文名搜索。某一页只有 1 到 3 条时全部列出，并保留栏目上的名字。"""
    japanese = _japanese_source(original, title)
    terms = _japanese_terms(japanese, must_find) if japanese else []
    if not terms:
        return {"ok": False, "reason": "没有日文名，GGBase 只按日文搜索", "pages": []}
    try:
        for index, keyword in enumerate(terms):
            if index:
                time.sleep(0.2)
            items = _search_once(keyword)
            if 0 < len(items) < 4:
                return _pack(_as_pages(items), judge=True)
            matched = [item for item in items if _full_or_half(japanese, item["name"])]
            if not matched:
                continue
            matched.sort(key=lambda item: (-_similarity(japanese, item["name"]), len(item["name"])))
            return _pack(_as_pages(matched[:3]), judge=False)
    except requests.RequestException:
        return {"ok": False, "reason": "GGBase 暂时连不上", "pages": []}
    return {"ok": False, "reason": "GGBase 用日文名和片段搜过，没有可列出的栏目", "pages": []}
