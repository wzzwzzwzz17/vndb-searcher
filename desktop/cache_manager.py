# -*- coding: utf-8 -*-
"""本地 JSON 文件缓存，减少重复 VNDB 请求。"""

from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any, Optional

import config


class CacheManager:
    def __init__(self, cache_dir: str | None = None, ttl: int | None = None) -> None:
        self.cache_dir = cache_dir or config.CACHE_DIR
        self.ttl = ttl if ttl is not None else config.CACHE_TTL
        os.makedirs(self.cache_dir, exist_ok=True)

    @staticmethod
    def _key(payload: Any) -> str:
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _path(self, key: str) -> str:
        return os.path.join(self.cache_dir, f"{key}.json")

    def get(self, payload: Any) -> Optional[Any]:
        path = self._path(self._key(payload))
        if not os.path.isfile(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if time.time() - data.get("_ts", 0) > self.ttl:
                try:
                    os.remove(path)
                except OSError:
                    pass
                return None
            return data.get("value")
        except (OSError, json.JSONDecodeError, TypeError, KeyError):
            return None

    def set(self, payload: Any, value: Any) -> None:
        path = self._path(self._key(payload))
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"_ts": time.time(), "value": value}, f, ensure_ascii=False)
        except OSError:
            pass
