# -*- coding: utf-8 -*-
"""VNDB 全量标签库：拉取、本地缓存、按需刷新。"""

from __future__ import annotations

import json
import os
import time
from typing import Any

import requests

import config

TAG_CACHE_FILE = os.path.join(config.CACHE_DIR, "vndb_tags_full.json")
TAG_CACHE_TTL = 7 * 24 * 3600  # 7 天
TAG_FIELDS = "name, aliases, category, searchable, applicable, vn_count"


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "Content-Type": "application/json",
            "User-Agent": config.VNDB_USER_AGENT,
        }
    )
    return s


def fetch_all_tags(progress_cb=None) -> list[dict[str, Any]]:
    """分页拉取全部标签（约 3000+）。"""
    session = _session()
    tags: list[dict[str, Any]] = []
    page = 1
    while True:
        body = {
            "fields": TAG_FIELDS,
            "results": 100,
            "page": page,
            "sort": "id",
            "reverse": False,
        }
        resp = session.post(
            f"{config.VNDB_API_BASE.rstrip('/')}/tag",
            json=body,
            timeout=max(config.VNDB_TIMEOUT, 8.0),
        )
        if resp.status_code == 429:
            time.sleep(2.0)
            continue
        resp.raise_for_status()
        data = resp.json()
        batch = data.get("results") or []
        tags.extend(batch)
        if progress_cb:
            progress_cb(page, len(tags), bool(data.get("more")))
        if not data.get("more") or not batch:
            break
        page += 1
        time.sleep(0.35)
    return tags


def save_tags(tags: list[dict[str, Any]], path: str | None = None) -> str:
    path = path or TAG_CACHE_FILE
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {"_ts": time.time(), "count": len(tags), "tags": tags}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    return path


def load_cached_tags(path: str | None = None) -> list[dict[str, Any]] | None:
    path = path or TAG_CACHE_FILE
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if time.time() - float(data.get("_ts", 0)) > TAG_CACHE_TTL:
            return None
        tags = data.get("tags")
        return tags if isinstance(tags, list) and tags else None
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def ensure_tag_catalog(force_refresh: bool = False) -> list[dict[str, Any]]:
    """返回全量标签；无缓存或过期则自动拉取。"""
    if not force_refresh:
        cached = load_cached_tags()
        if cached:
            return cached
    tags = fetch_all_tags()
    if not tags:
        # 拉取失败时退回过期缓存
        path = TAG_CACHE_FILE
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                return list((json.load(f).get("tags") or []))
        raise RuntimeError("无法获取 VNDB 标签库")
    save_tags(tags)
    return tags


if __name__ == "__main__":
    def _p(page, n, more):
        print(f"page={page} total={n} more={more}")

    tags = fetch_all_tags(_p)
    save_tags(tags)
    print(f"saved {len(tags)} tags -> {TAG_CACHE_FILE}")
