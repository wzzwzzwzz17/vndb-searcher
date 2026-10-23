# -*- coding: utf-8 -*-
"""VNDB Kana API：校验、请求、限流、缓存、结果清洗。"""

from __future__ import annotations

import re
import time
from typing import Any, Optional

import requests

import config
from cache_manager import CacheManager

ALLOWED_FILTER_NAMES = {
    "id",
    "search",
    "lang",
    "olang",
    "platform",
    "length",
    "released",
    "rating",
    "votecount",
    "has_description",
    "has_anime",
    "has_screenshot",
    "has_review",
    "devstatus",
    "tag",
    "dtag",
    "anime_id",
    "label",
    "release",
    "character",
    "staff",
    "developer",
}
ALLOWED_OPS = {"=", "!=", ">=", ">", "<=", "<"}
ALLOWED_SORT = {"id", "title", "released", "rating", "votecount", "searchrank"}
TAG_ID_RE = re.compile(r"^g\d+$")
VN_FIELDS = (
    "title, alttitle, rating, description, released, votecount, "
    "tags.id, tags.rating, tags.spoiler"
)
VN_DETAIL_FIELDS = (
    "title, alttitle, rating, description, released, votecount, "
    "developers.name, developers.original, developers.id, "
    "tags.id, tags.rating, tags.spoiler"
)


class FilterValidationError(ValueError):
    pass


def normalize_filters(filters: Any) -> Any:
    """修复 LLM 常见失误：filters 被多包一层数组，如 [[\"and\", ...]]。"""
    node = filters
    for _ in range(5):
        if (
            isinstance(node, list)
            and len(node) == 1
            and isinstance(node[0], list)
            and node[0]
            and (
                node[0][0] in ("and", "or", "not")
                or (isinstance(node[0][0], str) and node[0][0] in ALLOWED_FILTER_NAMES)
            )
        ):
            node = node[0]
            continue
        break
    return node


def _validate_predicate(
    node: Any,
    depth: int = 0,
    allowed_tag_ids: set[str] | None = None,
) -> None:
    if depth > 12:
        raise FilterValidationError("Filter 嵌套过深")
    if not isinstance(node, list) or len(node) < 2:
        raise FilterValidationError(f"非法 Filter 节点: {node!r}")

    head = node[0]
    if head in ("and", "or"):
        if len(node) < 3:
            raise FilterValidationError(f"{head} 至少需要两个子条件")
        for child in node[1:]:
            _validate_predicate(child, depth + 1, allowed_tag_ids)
        return
    if head == "not":
        if len(node) != 2:
            raise FilterValidationError("not 只能有一个子条件")
        _validate_predicate(node[1], depth + 1, allowed_tag_ids)
        return

    if len(node) != 3:
        raise FilterValidationError(f"谓词必须是 [字段, 运算符, 值]: {node!r}")
    name, op, value = node
    if name not in ALLOWED_FILTER_NAMES:
        raise FilterValidationError(f"非法字段: {name}")
    if op not in ALLOWED_OPS:
        raise FilterValidationError(f"非法运算符: {op}")

    if name in ("tag", "dtag"):
        _validate_tag_value(value, allowed_tag_ids)
    if name == "id" and isinstance(value, str) and not re.match(r"^v\d+$", value):
        # 也允许裸整数，但禁止明显乱编
        if not isinstance(value, int):
            raise FilterValidationError(f"非法 VN id: {value}")
    if name == "rating" and isinstance(value, (int, float)):
        if not (10 <= float(value) <= 100):
            raise FilterValidationError("rating 必须在 10–100")


def _tag_id_of(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, list) and value:
        return value[0] if isinstance(value[0], str) else None
    return None


def _validate_tag_value(value: Any, allowed_tag_ids: set[str] | None = None) -> None:
    if isinstance(value, str):
        if not TAG_ID_RE.match(value):
            raise FilterValidationError(f"非法 tag ID: {value}")
        if allowed_tag_ids is not None and value not in allowed_tag_ids:
            raise FilterValidationError(f"tag ID 不在全库检索候选中: {value}")
        return
    if isinstance(value, list) and len(value) == 3:
        tid, spoiler, level = value
        if not isinstance(tid, str) or not TAG_ID_RE.match(tid):
            raise FilterValidationError(f"非法 tag ID: {tid}")
        if allowed_tag_ids is not None and tid not in allowed_tag_ids:
            raise FilterValidationError(f"tag ID 不在全库检索候选中: {tid}")
        if spoiler not in (0, 1, 2):
            raise FilterValidationError("tag spoiler 必须是 0/1/2")
        if not isinstance(level, (int, float)) or not (0 <= float(level) <= 3):
            raise FilterValidationError("tag level 必须在 0–3")
        return
    raise FilterValidationError(f"非法 tag 值: {value!r}")


def validate_query(query: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(query, dict):
        raise FilterValidationError("查询必须是对象")
    filters = query.get("filters")
    if filters is None:
        raise FilterValidationError("缺少 filters")
    filters = normalize_filters(filters)
    allowed_raw = query.get("_allowed_tag_ids")
    allowed_tag_ids: set[str] | None = None
    if isinstance(allowed_raw, (list, set, tuple)) and allowed_raw:
        allowed_tag_ids = set(allowed_raw)
    _validate_predicate(filters, allowed_tag_ids=allowed_tag_ids)

    sort = query.get("sort", "rating")
    if sort not in ALLOWED_SORT:
        raise FilterValidationError(f"非法 sort: {sort}")

    reverse = bool(query.get("reverse", True))
    limit = int(query.get("limit", config.DEFAULT_RESULT_LIMIT))
    if not (1 <= limit <= 20):
        raise FilterValidationError("limit 必须在 1–20")

    page = int(query.get("page") or 1)
    if page < 1:
        page = 1

    return {
        "filters": filters,
        "sort": sort,
        "reverse": reverse,
        "results": limit,
        "page": page,
        "fields": query.get("_fields") or VN_FIELDS,
    }


def force_single_core_query(query: dict[str, Any]) -> dict[str, Any]:
    """强制 filters 只保留一个核心 tag（保留 rating/released/search 等非 tag 条件）。"""
    filters = normalize_filters(query.get("filters"))
    roles = dict(query.get("tag_roles") or {})
    tag_preds: list[Any] = []
    other_preds: list[Any] = []

    def collect(node: Any) -> None:
        if not isinstance(node, list) or not node:
            return
        if node[0] == "and":
            for c in node[1:]:
                collect(c)
            return
        if node[0] == "or":
            # or 里若全是 tag，拆开收集；否则整段当 other
            if all(
                isinstance(c, list) and c and c[0] in ("tag", "dtag")
                for c in node[1:]
            ):
                for c in node[1:]:
                    collect(c)
            else:
                other_preds.append(node)
            return
        if node[0] == "not":
            other_preds.append(node)
            return
        if len(node) == 3 and node[0] in ("tag", "dtag"):
            tag_preds.append(node)
            return
        other_preds.append(node)

    collect(filters)

    def tag_id_of(pred: Any) -> str | None:
        if not (isinstance(pred, list) and len(pred) == 3):
            return None
        val = pred[2]
        if isinstance(val, str):
            return val
        if isinstance(val, list) and val and isinstance(val[0], str):
            return val[0]
        return None

    # 优先 tag_roles 标为 core 的；否则取第一个 tag
    core_ids = [tid for tid, role in roles.items() if role == "core"]
    chosen = None
    for pred in tag_preds:
        tid = tag_id_of(pred)
        if tid and tid in core_ids:
            chosen = pred
            break
    if chosen is None and tag_preds:
        chosen = tag_preds[0]

    kept_tags = [chosen] if chosen else []
    new_roles: dict[str, str] = {}
    if chosen:
        tid = tag_id_of(chosen)
        if tid:
            new_roles[tid] = "core"

    parts = [*other_preds, *kept_tags]
    if not parts:
        new_filters = filters
    elif len(parts) == 1:
        new_filters = parts[0]
    else:
        new_filters = ["and", *parts]

    out = dict(query)
    out["filters"] = new_filters
    out["tag_roles"] = new_roles
    return out


def exclude_ids_filter(filters: Any, exclude_ids: list[str]) -> Any:
    """在原 filters 上排除已展示过的作品 ID，尽量刷出不同游戏。"""
    ids = [i for i in exclude_ids if isinstance(i, str) and i.startswith("v")]
    if not ids:
        return filters
    # VNDB 单次 and 子句不宜过多，最多排除 30 个
    preds = [["id", "!=", vid] for vid in ids[-30:]]
    base = normalize_filters(filters)
    if isinstance(base, list) and base and base[0] == "and":
        return ["and", *base[1:], *preds]
    return ["and", base, *preds]


def ensure_min_year_filter(filters: Any, min_year: int) -> Any:
    """确保 filters 含 released >= 年份；已有则覆盖为用户设定。"""
    year_pred = ["released", ">=", str(int(min_year))]
    node = normalize_filters(filters)

    def strip_released(n: Any) -> Any:
        if not isinstance(n, list) or not n:
            return n
        if n[0] in ("and", "or"):
            kids = []
            for c in n[1:]:
                sc = strip_released(c)
                if sc is None:
                    continue
                kids.append(sc)
            if not kids:
                return None
            if len(kids) == 1 and n[0] == "and":
                return kids[0]
            return [n[0], *kids]
        if n[0] == "not":
            return n
        if len(n) == 3 and n[0] == "released":
            return None
        return n

    cleaned = strip_released(node)
    if cleaned is None:
        return year_pred
    if isinstance(cleaned, list) and cleaned and cleaned[0] == "and":
        return ["and", *cleaned[1:], year_pred]
    return ["and", cleaned, year_pred]


def filter_items_by_min_year(items: list[dict], min_year: int) -> list[dict]:
    """本地再滤一层发售年份（released 可能是 YYYY / YYYY-MM / YYYY-MM-DD）。"""
    out = []
    for it in items:
        released = it.get("released") or ""
        m = re.match(r"^(\d{4})", str(released))
        if not m:
            # 未知日期保守保留
            out.append(it)
            continue
        if int(m.group(1)) >= int(min_year):
            out.append(it)
    return out


def relax_filters(filters: Any, search_mode: str | None = None) -> Any:
    """结果为 0 时按模式宽松一轮。硬核模式不宽松（允许空结果）。"""
    if not isinstance(filters, list):
        return filters

    mode = search_mode or config.SEARCH_MODE_QUALITY
    if mode == config.SEARCH_MODE_HARDCORE:
        return filters  # 硬核：保持条件，允许找不到

    def lower_tag_level(value: Any, floor: float) -> Any:
        if isinstance(value, list) and len(value) == 3:
            tid, spoiler, level = value
            try:
                lv = float(level)
            except (TypeError, ValueError):
                lv = floor
            return [tid, spoiler, max(floor, round(lv - 0.4, 1))]
        return value

    def walk(node: Any) -> Any:
        if not isinstance(node, list) or not node:
            return node
        head = node[0]
        if head in ("and", "or"):
            children = []
            for c in node[1:]:
                nc = walk(c)
                if nc is None:
                    continue
                children.append(nc)
            if not children:
                return ["rating", ">=", 50]
            if len(children) == 1 and head == "and":
                return children[0]
            return [head, *children]
        if head == "not":
            return node
        if len(node) == 3 and node[0] == "rating" and node[1] in (">=", ">"):
            if mode == config.SEARCH_MODE_QUALITY:
                return ["rating", ">=", 65]
            return ["rating", ">=", 50]
        if len(node) == 3 and node[0] in ("tag", "dtag"):
            if mode == config.SEARCH_MODE_CONTENT:
                return [node[0], node[1], lower_tag_level(node[2], 1.6)]
            return None
        # 不放宽年份
        if len(node) == 3 and node[0] == "released":
            return node
        return node

    relaxed = walk(filters)
    if relaxed is None:
        return ["rating", ">=", 50]
    return relaxed


def _strip_bbcode(text: str) -> str:
    text = re.sub(r"\[url=[^\]]*\](.*?)\[/url\]", r"\1", text, flags=re.I | re.S)
    text = re.sub(r"\[/?[^\]]+\]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def clean_results(raw: dict[str, Any], desc_limit: int = 280) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in raw.get("results") or []:
        vid = item.get("id") or ""
        rating = item.get("rating")
        desc = item.get("description") or ""
        if isinstance(desc, str):
            desc = _strip_bbcode(desc)
            if desc_limit and len(desc) > desc_limit:
                desc = desc[: desc_limit - 3] + "..."
        developers = []
        for d in item.get("developers") or []:
            if isinstance(d, dict):
                developers.append(
                    {
                        "id": d.get("id"),
                        "name": d.get("name") or "",
                        "original": d.get("original") or "",
                    }
                )
        raw_tags = []
        for t in item.get("tags") or []:
            if isinstance(t, dict) and t.get("id"):
                raw_tags.append(
                    {
                        "id": t.get("id"),
                        "rating": t.get("rating"),
                        "spoiler": t.get("spoiler"),
                    }
                )
        out.append(
            {
                "id": vid,
                "title": item.get("title") or "",
                "original": item.get("alttitle") or "",
                "rating": round(rating / 10, 2) if isinstance(rating, (int, float)) else None,
                "rating_raw": rating,
                "description": desc,
                "released": item.get("released"),
                "votecount": item.get("votecount"),
                "developers": developers,
                "raw_tags": raw_tags,
                "url": f"https://vndb.org/{vid}" if vid else "",
            }
        )
    return out


class VndbClient:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Content-Type": "application/json",
                "User-Agent": config.VNDB_USER_AGENT,
            }
        )
        self.cache = CacheManager()
        self._last_request_ts = 0.0
        self._min_interval = 0.35  # 本地软限流，避免连打

    def _throttle(self) -> None:
        elapsed = time.time() - self._last_request_ts
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)

    def search_vn(
        self,
        query: dict[str, Any],
        use_cache: bool = True,
        page: int = 1,
        exclude_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        q = dict(query)
        if exclude_ids:
            q["filters"] = exclude_ids_filter(q.get("filters"), exclude_ids)
        q["page"] = page
        body = validate_query(q)
        cache_key = {"endpoint": "vn", **body}
        if use_cache:
            cached = self.cache.get(cache_key)
            if cached is not None:
                return cached

        data = self._post("/vn", body)
        cleaned = clean_results(data)
        self.cache.set(cache_key, cleaned)
        return cleaned

    def get_vn_detail(self, vn_id: str) -> dict[str, Any]:
        """拉取单部作品的剧情简介与开发商/出版社信息。"""
        body = {
            "filters": ["id", "=", vn_id],
            "fields": VN_DETAIL_FIELDS,
            "results": 1,
        }
        data = self._post("/vn", body)
        cleaned = clean_results(data, desc_limit=0)
        if not cleaned:
            raise RuntimeError(f"未找到作品 {vn_id}")
        return cleaned[0]

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        url = config.VNDB_API_BASE.rstrip("/") + path
        last_err: Optional[Exception] = None
        for attempt in range(2):  # 首次 + 重试 1 次
            self._throttle()
            try:
                self._last_request_ts = time.time()
                resp = self.session.post(url, json=body, timeout=max(config.VNDB_TIMEOUT, 5.0))
                if resp.status_code == 429:
                    time.sleep(1.5 * (attempt + 1))
                    last_err = RuntimeError("VNDB 限流 (429)，请稍后再试")
                    continue
                if resp.status_code >= 400:
                    raise RuntimeError(f"VNDB HTTP {resp.status_code}: {resp.text[:300]}")
                return resp.json()
            except requests.Timeout as e:
                last_err = e
                time.sleep(0.5)
            except requests.RequestException as e:
                last_err = e
                time.sleep(0.5)
        raise RuntimeError(f"VNDB 请求失败: {last_err}")
