# -*- coding: utf-8 -*-
"""VNDB 官方标签库（含主父标签）读取与加权检索。"""

from __future__ import annotations

import json
import os
from typing import Any

import config
from tag_i18n import TAG_ZH
from vndb_api import VndbClient

_HERE = os.path.dirname(os.path.abspath(__file__))
TREE_CANDIDATES = [
    os.path.join(config.CACHE_DIR, "vndb_tags_tree.json"),
    os.path.join(_HERE, "data", "vndb_tags_tree.json"),
]
ZH_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "android_app", "app", "src", "main", "assets", "www", "tag_zh.json")
)
CAT_ZH = {"cont": "内容", "ero": "性内容", "tech": "技术"}

_TREE_CACHE: list[dict[str, Any]] | None = None


def _tree_path() -> str:
    for path in TREE_CANDIDATES:
        if os.path.isfile(path):
            return path
    raise FileNotFoundError("缺少 VNDB 标签树文件 vndb_tags_tree.json")


def _zh_map() -> dict[str, str]:
    merged = dict(TAG_ZH)
    if os.path.isfile(ZH_PATH):
        try:
            with open(ZH_PATH, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                merged.update({str(k): str(v) for k, v in data.items() if v})
        except (OSError, json.JSONDecodeError):
            pass
    return merged


def label_of(tag_id: str, english: str, names: dict[str, str]) -> str:
    zh = names.get(tag_id) or ""
    if zh:
        return zh
    return english or tag_id


def load_tree() -> list[dict[str, Any]]:
    global _TREE_CACHE
    if _TREE_CACHE is not None:
        return _TREE_CACHE
    with open(_tree_path(), "r", encoding="utf-8") as handle:
        raw = json.load(handle)
    names = _zh_map()
    nodes: dict[str, dict[str, Any]] = {}
    for row in raw:
        tag_id = f"g{int(row['id'])}"
        english = row.get("name") or ""
        nodes[tag_id] = {
            "id": tag_id,
            "en": english,
            "zh": label_of(tag_id, english, names),
            "cat": CAT_ZH.get(row.get("cat") or "", row.get("cat") or ""),
            "searchable": bool(row.get("searchable")),
            "applicable": bool(row.get("applicable")),
            "parents": [f"g{int(pid)}" for pid in (row.get("parents") or [])],
            "children": [],
        }
    roots: list[dict[str, Any]] = []
    for node in nodes.values():
        parents = [pid for pid in node["parents"] if pid in nodes]
        node["parents"] = parents
        node["also"] = [nodes[pid]["zh"] for pid in parents[1:]]
        if not parents:
            roots.append(node)
        else:
            nodes[parents[0]]["children"].append(node)

    def sort_key(item: dict[str, Any]) -> str:
        return item["zh"]

    def sort_node(item: dict[str, Any]) -> None:
        item["children"].sort(key=sort_key)
        for child in item["children"]:
            sort_node(child)

    roots.sort(key=sort_key)
    for root in roots:
        sort_node(root)
    _TREE_CACHE = roots
    return roots


def clamp_level(value: Any) -> float:
    try:
        level = float(value)
    except (TypeError, ValueError):
        level = 0.6
    return min(2.8, max(0.6, round(level, 1)))


def search_weighted_tags(selected: list[dict[str, Any]], exclude_ids: list[str] | None = None) -> list[dict[str, Any]]:
    predicates: list[Any] = []
    for row in selected:
        tag_id = str(row.get("id") or "")
        if not tag_id.startswith("g"):
            continue
        predicates.append(["tag", "=", [tag_id, 0, clamp_level(row.get("level"))]])
    if not predicates:
        raise ValueError("请至少添加一个标签")
    for vn_id in (exclude_ids or [])[-25:]:
        if isinstance(vn_id, str) and vn_id.startswith("v"):
            predicates.append(["id", "!=", vn_id])
    predicates.append(["olang", "=", "ja"])
    filters: Any = predicates[0] if len(predicates) == 1 else ["and", *predicates]
    items = VndbClient().search_vn(
        {
            "filters": filters,
            "sort": "rating",
            "reverse": True,
            "limit": 10,
            "_allowed_tag_ids": [row[2][0] for row in predicates if row[0] == "tag"],
        },
        use_cache=False,
    )
    from community_scores import attach_community_scores
    from result_enrich import enrich_result_items
    from vndb_api import filter_items_japanese

    items = filter_items_japanese(items)

    search_ids = {row[2][0] for row in predicates if row[0] == "tag"}
    enrich_result_items(items, search_ids, top_n=4)
    attach_community_scores(items)
    return items
