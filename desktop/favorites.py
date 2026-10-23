# -*- coding: utf-8 -*-
"""收藏夹：存档于项目根目录 favorites.json。"""

from __future__ import annotations

import json
import os
import time
from typing import Any

import config


def _empty() -> dict[str, Any]:
    return {"updated_at": None, "items": []}


def load_favorites(path: str | None = None) -> dict[str, Any]:
    path = path or config.FAVORITES_FILE
    if not os.path.isfile(path):
        return _empty()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return _empty()
        items = data.get("items")
        if not isinstance(items, list):
            data["items"] = []
        return data
    except (OSError, json.JSONDecodeError):
        return _empty()


def save_favorites(data: dict[str, Any], path: str | None = None) -> str:
    path = path or config.FAVORITES_FILE
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    data = dict(data)
    data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def add_items(new_items: list[dict[str, Any]], path: str | None = None) -> int:
    """按 id 去重追加，返回新增条数。"""
    data = load_favorites(path)
    existing = {it.get("id") for it in data["items"] if isinstance(it, dict)}
    added = 0
    for it in new_items:
        if not isinstance(it, dict):
            continue
        vid = it.get("id")
        if not vid or vid in existing:
            continue
        data["items"].append(
            {
                "id": vid,
                "title": it.get("title") or "",
                "original": it.get("original") or "",
                "rating": it.get("rating"),
                "released": it.get("released"),
                "url": it.get("url") or f"https://vndb.org/{vid}",
                "description": it.get("description") or "",
                "added_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
        existing.add(vid)
        added += 1
    save_favorites(data, path)
    return added


def remove_ids(ids: list[str], path: str | None = None) -> int:
    data = load_favorites(path)
    idset = set(ids)
    before = len(data["items"])
    data["items"] = [
        it for it in data["items"] if not (isinstance(it, dict) and it.get("id") in idset)
    ]
    save_favorites(data, path)
    return before - len(data["items"])


def list_items(path: str | None = None) -> list[dict[str, Any]]:
    return list(load_favorites(path).get("items") or [])
