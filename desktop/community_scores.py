# -*- coding: utf-8 -*-
"""鲲 Galgame、TouchGal 的作品评分。只读取评分，不读取下载或磁力链接。"""

from __future__ import annotations

import random
import re
import time
from typing import Any

import requests

import config

_SKIP_KEYS = {
    "download",
    "downloads",
    "magnet",
    "resource",
    "resources",
    "pan",
    "url",
    "link",
    "links",
    "file",
    "files",
}
_NAME_KEYS = {"name", "title", "original", "name_cn", "name_jp", "name_en", "zh-cn", "ja-jp", "en-us"}
_SCORE_KEYS = {"rating", "score", "avg", "average", "avg_rating", "rating_avg"}
_COUNT_KEYS = {"rating_count", "ratingcount", "votes", "votecount", "score_count", "count"}

_cache: dict[str, dict[str, Any]] = {}
_down: set[str] = set()


def _norm(text: str) -> str:
    return re.sub(r"[\s\*＊·・]+", "", (text or "").strip().lower())


def _as_score(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    if n > 10:
        n = n / 10.0
    if n > 10:
        return None
    return round(n, 2)


def _names_from(obj: dict[str, Any]) -> list[str]:
    found: list[str] = []

    def walk(node: Any, key: str) -> None:
        if key.lower() in _SKIP_KEYS:
            return
        if isinstance(node, str) and key.lower() in _NAME_KEYS and node.strip():
            found.append(node.strip())
        elif isinstance(node, dict):
            for k, v in node.items():
                walk(v, str(k))

    for k, v in obj.items():
        walk(v, str(k))
    return found


def _score_from(obj: dict[str, Any]) -> tuple[float | None, int | None]:
    score = None
    count = None
    for k, v in obj.items():
        lk = str(k).lower()
        if lk in _SKIP_KEYS:
            continue
        if score is None and lk in _SCORE_KEYS:
            score = _as_score(v)
        if count is None and lk in _COUNT_KEYS and isinstance(v, (int, float)) and not isinstance(v, bool):
            count = int(v)
    return score, count


def _match_item(obj: dict[str, Any], queries: list[str]) -> dict[str, Any] | None:
    names = _names_from(obj)
    if not names:
        return None
    blob = _norm(" ".join(names))
    if not any(q and (q in blob or blob in q) for q in queries):
        return None
    score, count = _score_from(obj)
    if score is None:
        return None
    return {"name": names[0], "score": score, "votes": count}


def _walk_hits(node: Any, queries: list[str]) -> dict[str, Any] | None:
    if isinstance(node, dict):
        hit = _match_item(node, queries)
        if hit:
            return hit
        for k, v in node.items():
            if str(k).lower() in _SKIP_KEYS:
                continue
            found = _walk_hits(v, queries)
            if found:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _walk_hits(item, queries)
            if found:
                return found
    return None


def _get_json(url: str, params: dict[str, Any], headers: dict[str, str]) -> tuple[int, Any]:
    resp = requests.get(
        url,
        params=params,
        headers=headers,
        timeout=6,
    )
    if resp.status_code != 200:
        return resp.status_code, None
    try:
        return 200, resp.json()
    except ValueError:
        return resp.status_code, None


def _lookup_site(site: str, queries: list[str]) -> dict[str, Any]:
    if site in _down:
        return {"ok": False, "reason": "本轮已跳过"}
    headers = {"User-Agent": config.VNDB_USER_AGENT, "Accept": "application/json"}
    if site == "kungal":
        return _lookup_kungal_v1(queries)
    else:
        base = config.TOUCHGAL_API_BASE.rstrip("/")
        token = config.TOUCHGAL_API_TOKEN
        if token:
            headers["Authorization"] = f"Bearer {token}"
        urls = [f"{base}/api/galgame"]
        param_keys = ("q", "keywords")

    keyword = queries[0] if queries else ""
    last_status = 0
    for url in urls:
        for key in param_keys:
            try:
                status, data = _get_json(url, {key: keyword, "limit": 5, "page": 1}, headers)
            except requests.RequestException:
                last_status = 0
                continue
            last_status = status
            if status in (401, 403):
                _down.add(site)
                return {"ok": False, "reason": "需要登录或被站点拦截"}
            if status == 200 and data is not None:
                hit = _walk_hits(data, [_norm(text) for text in queries])
                if hit:
                    hit["ok"] = True
                    return hit
    if last_status == 0:
        return {"ok": False, "reason": "站点暂时连不上"}
    return {"ok": False, "reason": "没有对应评分"}


def _raw_queries(item: dict[str, Any]) -> list[str]:
    raw = []
    for key in ("original", "title"):
        text = (item.get(key) or "").strip()
        if text and text not in raw:
            raw.append(text)
    return raw


def _queries(item: dict[str, Any]) -> list[str]:
    return _raw_queries(item)


def _lookup_kungal_v1(queries: list[str]) -> dict[str, Any]:
    keyword = queries[0] if queries else ""
    try:
        status, data = _get_json(
            "https://www.kungal.com/api/v1/search/works",
            {"q": keyword, "page": 1, "limit": 8, "include_nsfw": "true"},
            _headers(),
        )
    except requests.RequestException:
        return {"ok": False, "reason": "站点暂时连不上"}
    if status in (401, 403):
        return {"ok": False, "reason": "需要登录或被站点拦截"}
    if status != 200 or not isinstance(data, dict):
        return {"ok": False, "reason": "没有对应评分"}
    work = _pick_work(data.get("items") or [], queries)
    score = _as_score(work.get("rating_score")) if isinstance(work, dict) else None
    if not work or score is None:
        return {"ok": False, "reason": "没有对应评分"}
    names = _kun_names(work)
    return {
        "ok": True,
        "score": score,
        "votes": work.get("rating_count"),
        "name": names[0] if names else "",
    }


def _composite(vndb: Any, extra: dict[str, Any]) -> float | None:
    nums = []
    if isinstance(vndb, (int, float)):
        nums.append(float(vndb))
    for site in ("kungal", "touchgal"):
        row = extra.get(site) or {}
        if row.get("ok") and isinstance(row.get("score"), (int, float)):
            nums.append(float(row["score"]))
    if not nums:
        return None
    return round(sum(nums) / len(nums), 2)


def attach_community_scores(items: list[dict[str, Any]]) -> None:
    """为每部作品补上鲲 / TouchGal 评分，并按综合分重新排序。"""
    for it in items:
        key = "|".join(_queries(it)) or str(it.get("id") or "")
        if key in _cache:
            extra = _cache[key]
        else:
            queries = _queries(it)
            extra = {
                "kungal": _lookup_site("kungal", queries) if queries else {"ok": False, "reason": "无标题"},
                "touchgal": _lookup_site("touchgal", queries) if queries else {"ok": False, "reason": "无标题"},
            }
            _cache[key] = extra
            time.sleep(0.25)
        it["community"] = extra
        it["composite"] = _composite(it.get("rating"), extra)


def score_line(item: dict[str, Any]) -> str:
    def one(label: str, row: dict[str, Any] | None) -> str:
        if not row or not row.get("ok"):
            reason = (row or {}).get("reason") or "无"
            return f"{label} —"
        votes = row.get("votes")
        tail = f"（{votes}人）" if votes else ""
        return f"{label} {row['score']:.2f}{tail}"

    community = item.get("community") or {}
    vndb = item.get("rating")
    vndb_s = f"{vndb:.2f}" if isinstance(vndb, (int, float)) else "—"
    comp = item.get("composite")
    comp_s = f"{comp:.2f}" if isinstance(comp, (int, float)) else "—"
    return (
        f"VNDB {vndb_s} | {one('鲲', community.get('kungal'))} | "
        f"{one('TouchGal', community.get('touchgal'))} | 综合 {comp_s}"
    )


def _headers(token: str = "") -> dict[str, str]:
    headers = {"User-Agent": config.VNDB_USER_AGENT, "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _kun_names(work: dict[str, Any]) -> list[str]:
    names = [work.get("display_name") or ""]
    localized = work.get("localized") or {}
    if isinstance(localized, dict):
        for value in localized.values():
            if isinstance(value, dict) and value.get("value"):
                names.append(str(value["value"]))
    return [name.strip() for name in names if name and name.strip()]


def _pick_work(items: list[dict[str, Any]], queries: list[str]) -> dict[str, Any] | None:
    norms = [_norm(q) for q in queries if _norm(q)]
    best = None
    best_rank = 0
    for work in items:
        if not isinstance(work, dict):
            continue
        names = _kun_names(work) or _names_from(work)
        blobs = [_norm(name) for name in names]
        rank = 0
        for query in norms:
            for blob in blobs:
                if query == blob:
                    rank = max(rank, 3)
                elif query in blob or blob in query:
                    rank = max(rank, 2)
        if rank > best_rank:
            best = work
            best_rank = rank
    return best


def _intro_text(intros: Any) -> str:
    if not isinstance(intros, list):
        return ""
    by_locale: dict[str, str] = {}
    for row in intros:
        if isinstance(row, dict) and row.get("value"):
            by_locale[str(row.get("locale") or "")] = str(row["value"]).strip()
    for locale in ("zh-Hans", "zh-CN", "zh", "zh-Hant", "ja", "en"):
        if by_locale.get(locale):
            return by_locale[locale]
    return next((text for text in by_locale.values() if text), "")


def _tag_names(tags: Any) -> list[str]:
    names: list[str] = []
    if not isinstance(tags, list):
        return names
    for tag in tags:
        if isinstance(tag, str) and tag.strip():
            names.append(tag.strip())
        elif isinstance(tag, dict):
            label = tag.get("display_name") or tag.get("name") or tag.get("zh") or ""
            if isinstance(label, str) and label.strip():
                names.append(label.strip())
    deduped: list[str] = []
    for name in names:
        if name not in deduped:
            deduped.append(name)
    return deduped[:30]


def _kun_detail(queries: list[str]) -> dict[str, Any]:
    try:
        status, data = _get_json(
            "https://www.kungal.com/api/v1/search/works",
            {"q": queries[0], "page": 1, "limit": 8, "include_nsfw": "true"},
            _headers(),
        )
    except requests.RequestException:
        return {"ok": False, "reason": "鲲站点暂时连不上"}
    if status != 200 or not isinstance(data, dict):
        return {"ok": False, "reason": "鲲没有返回作品资料"}
    work = _pick_work(data.get("items") or [], queries)
    if not work or not work.get("id"):
        return {"ok": False, "reason": "鲲没有匹配到这部作品"}
    try:
        detail_status, detail = _get_json(
            f"https://www.kungal.com/api/v1/works/{work['id']}",
            {},
            _headers(),
        )
    except requests.RequestException:
        detail_status, detail = 0, None
    source = detail if detail_status == 200 and isinstance(detail, dict) else work
    return {
        "ok": True,
        "name": (_kun_names(source) or [""])[0],
        "score": _as_score(source.get("rating_score")),
        "intro": _intro_text(source.get("intros")),
        "tags": _tag_names(source.get("tags")),
        "page": f"https://www.kungal.com/galgame/{work['id']}",
    }


def _touch_detail(queries: list[str], token: str) -> dict[str, Any]:
    if not token:
        return {"ok": False, "reason": "未配置 TouchGal Token"}
    keyword = queries[0]
    if len(keyword) < 3:
        return {"ok": False, "reason": "关键词太短，TouchGal 不接受"}
    try:
        status, data = _get_json(
            "https://developer.touchgal.com/api/v1/games/search",
            {"keyword": keyword, "page": 1, "limit": 8, "allowNsfw": "true"},
            _headers(token),
        )
    except requests.RequestException:
        return {"ok": False, "reason": "TouchGal 暂时连不上"}
    if status in (401, 403):
        return {"ok": False, "reason": "TouchGal Token 无效或被拦截"}
    if status != 200 or not isinstance(data, dict):
        return {"ok": False, "reason": "TouchGal 没有返回作品资料"}
    items = ((data.get("data") or {}).get("items") if isinstance(data.get("data"), dict) else None) or []
    game = _pick_work(items, queries)
    if not game:
        return {"ok": False, "reason": "TouchGal 没有匹配到这部作品"}
    unique_id = game.get("uniqueId") or game.get("id")
    detail = game
    if unique_id:
        try:
            detail_status, fetched = _get_json(
                f"https://developer.touchgal.com/api/v1/games/{unique_id}",
                {},
                _headers(token),
            )
            if detail_status == 200 and isinstance(fetched, dict):
                detail = fetched.get("data") if isinstance(fetched.get("data"), dict) else fetched
        except requests.RequestException:
            pass
    intro = ""
    for key in ("introduction", "intro", "description", "summary", "content"):
        value = detail.get(key) if isinstance(detail, dict) else None
        if isinstance(value, str) and value.strip():
            intro = value.strip()
            break
    tags = _tag_names(detail.get("tags") if isinstance(detail, dict) else None)
    page = f"https://www.touchgal.ink/{unique_id}" if unique_id else ""
    return {
        "ok": True,
        "name": (detail.get("name") if isinstance(detail, dict) else "") or game.get("name") or "",
        "score": _as_score((detail or {}).get("rating") if isinstance(detail, dict) else None),
        "intro": intro,
        "tags": tags,
        "page": page,
    }


def _has_kana(text: str) -> bool:
    return bool(re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", text or ""))


def _add_term(terms: list[str], text: str) -> None:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    if len(_norm(cleaned)) < 2 or cleaned in terms:
        return
    terms.append(cleaned)


def _shinnku_terms(queries: list[str], must_find: bool) -> list[str]:
    """全日文名、英文名，再取连续一半，最后多次随机取四分之一。"""
    japanese = [text for text in queries if _has_kana(text)]
    others = [text for text in queries if text not in japanese]
    terms: list[str] = []
    for text in japanese + others:
        _add_term(terms, text)
    source = japanese[0] if japanese else (queries[0] if queries else "")
    chars = list(source)
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
                _add_term(terms, source)
                break
            start = random.randint(0, count - quarter)
            _add_term(terms, "".join(chars[start : start + quarter]))
    return terms[: 12 if must_find else 8]


def _shinnku_entry(url: str, fragment: str) -> str:
    """把命中的文件收成「路径里带这段日文」的目录页。对不上就不收大目录。"""
    frag = _norm(fragment)
    if not frag or url.lower().startswith("magnet:"):
        return ""
    bare = url.split("?", 1)[0]
    pieces = [part for part in bare.split("/") if part]
    if bare.startswith("http"):
        prefix = bare.split("://", 1)[0] + "://" + pieces[0]
        segments = pieces[1:]
    else:
        prefix = "https://www.shinnku.com"
        segments = pieces
    decoded = [requests.utils.unquote(part) for part in segments]
    if decoded and re.search(r"\.[A-Za-z0-9]{1,5}$", decoded[-1]):
        decoded = decoded[:-1]
        segments = segments[:-1]
    for index in range(len(decoded) - 1, -1, -1):
        if frag in _norm(decoded[index]):
            return prefix + "/" + "/".join(segments[: index + 1]) + "/"
    return ""


def _search_shinnku_once(keyword: str) -> list[dict[str, str]]:
    try:
        response = requests.get(
            "https://www.shinnku.com/search",
            params={"q": keyword},
            headers=_headers(),
            timeout=8,
        )
    except requests.RequestException:
        return []
    if response.status_code != 200:
        return []
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    pattern = re.compile(r'hover:underline" href="([^"]+)">\s*([^<]+?)\s*</a>', re.I)
    search_page = "https://www.shinnku.com/search?q=" + requests.utils.quote(keyword)
    for match in pattern.finditer(response.text):
        href = match.group(1).strip()
        name = re.sub(r"\s+", " ", match.group(2)).strip()
        if href.lower().startswith("magnet:"):
            continue
        url = href if href.startswith("http") else "https://www.shinnku.com" + (href if href.startswith("/") else "/" + href)
        if _norm(keyword) not in _norm(name + " " + requests.utils.unquote(url)):
            continue
        folder = _shinnku_entry(url, keyword)
        page = folder or search_page
        label = name or keyword
        key = page + "|" + label
        if key in seen:
            continue
        seen.add(key)
        found.append({"name": label, "page": page})
        if len(found) >= 5:
            break
    return found


def _shinnku_pages(queries: list[str], must_find: bool = False) -> dict[str, Any]:
    """全名搜不到时，用日文名的连续片段再搜多次。"""
    terms = _shinnku_terms(queries, must_find)
    if not terms:
        return {"ok": False, "reason": "没有可用于真红小站的名字", "pages": []}
    pages: list[dict[str, str]] = []
    seen: set[str] = set()
    tried = 0
    for keyword in terms:
        if len(pages) >= 3:
            break
        tried += 1
        for item in _search_shinnku_once(keyword):
            marker = _norm(item["name"])
            if marker in seen:
                continue
            seen.add(marker)
            pages.append(item)
            if len(pages) >= 3:
                break
        time.sleep(0.2)
    if not pages:
        return {
            "ok": False,
            "reason": f"真红小站搜了 {tried} 次日文或英文片段，仍没有对上的目录",
            "pages": [],
        }
    return {
        "ok": True,
        "name": pages[0]["name"],
        "page": pages[0]["page"],
        "pages": pages,
        "intro": "",
        "tags": [],
    }


def fetch_work_profile(
    title: str,
    original: str,
    touchgal_token: str = "",
    must_find: bool = False,
) -> dict[str, Any]:
    """返回简介、标签、评分，以及三个站的作品参考页入口。"""
    queries: list[str] = []
    for text in (original, title):
        cleaned = (text or "").strip()
        if cleaned and cleaned not in queries:
            queries.append(cleaned)
    if not queries:
        empty = {"ok": False, "reason": "没有作品名"}
        return {"kungal": empty, "touchgal": empty, "shinnku": empty}
    token = (touchgal_token or config.TOUCHGAL_API_TOKEN or "").strip()
    return {
        "kungal": _kun_detail(queries),
        "touchgal": _touch_detail(queries, token),
        "shinnku": _shinnku_pages(queries, must_find),
    }
