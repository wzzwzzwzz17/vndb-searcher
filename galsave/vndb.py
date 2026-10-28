# -*- coding: utf-8 -*-
"""VNDB 分级拉取：只用官方原始语言为日语（olang=ja）的作品。

VNDB 的 rating 在 API 里是 10-100 的整数，站上显示为 1.0-10.0。
所以配置里的 8.0 档要传 rating >= 80。

排序固定 rating 降序：同一档里先处理高分。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Iterator

import requests

FIELDS = "id, title, alttitle, olang, rating, votecount, released, aliases, developers.name, developers.original"
USER_AGENT = "galsave/0.1 (personal-collection; https://github.com/wzzwzzwzz17/vndb-searcher)"


class VndbError(RuntimeError):
    """VNDB 请求失败（网络错误、限流、参数错误）。"""


@dataclass
class Vn:
    id: str
    title: str
    alttitle: str
    olang: str
    rating: float | None
    released: str | None = None
    aliases: list[str] = field(default_factory=list)
    developers: list[str] = field(default_factory=list)

    @property
    def display_rating(self) -> float | None:
        return round(self.rating / 10, 2) if isinstance(self.rating, (int, float)) else None

    @property
    def names(self) -> list[str]:
        """可用于搜索/匹配的名字，日文原名优先。"""
        out: list[str] = []
        for name in [self.alttitle, self.title, *self.aliases]:
            name = (name or "").strip()
            if name and name not in out:
                out.append(name)
        return out

    @property
    def preferred_name(self) -> str:
        """入库命名用：优先 VNDB 上的日文原名，没有日文名才退回英文标题。

        VNDB 的 `title` 通常是罗马字/英文，`alttitle` 才是原始语言的标题；
        官方语言是日语的作品，`alttitle` 就是日文名。
        """
        alt = (self.alttitle or "").strip()
        title = (self.title or "").strip()
        if alt and _has_japanese(alt):
            return alt
        if self.olang == "ja" and alt:
            return alt
        return title or alt or self.id

    @property
    def url(self) -> str:
        return f"https://vndb.org/{self.id}"


def _has_japanese(text: str) -> bool:
    """有没有假名或汉字（罗马字标题基本不含）。"""
    for ch in str(text or ""):
        code = ord(ch)
        if 0x3040 <= code <= 0x30FF or 0x4E00 <= code <= 0x9FFF or 0x3400 <= code <= 0x4DBF:
            return True
    return False


def _to_vn(item: dict[str, Any]) -> Vn:
    devs = []
    for dev in item.get("developers") or []:
        name = (dev.get("original") or dev.get("name") or "").strip()
        if name and name not in devs:
            devs.append(name)
    aliases = [a.strip() for a in (item.get("aliases") or []) if isinstance(a, str) and a.strip()]
    return Vn(
        id=str(item.get("id") or ""),
        title=str(item.get("title") or ""),
        alttitle=str(item.get("alttitle") or ""),
        olang=str(item.get("olang") or ""),
        rating=item.get("rating"),
        released=item.get("released"),
        aliases=aliases,
        developers=devs,
    )


class VndbClient:
    def __init__(self, cfg: dict[str, Any]):
        vcfg = cfg.get("vndb") or {}
        self.base_url = str(vcfg.get("base_url") or "https://api.vndb.org/kana").rstrip("/")
        self.timeout = float(vcfg.get("timeout") or 30)
        self.page_size = int(vcfg.get("page_size") or 100)
        self.interval = float(vcfg.get("request_interval") or 0.5)
        self.max_retry = int(cfg.get("max_retry") or 3)
        proxy = str(vcfg.get("proxy") or "").strip()
        self.proxies = {"http": proxy, "https": proxy} if proxy else None
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Content-Type": "application/json"})
        self._last_request = 0.0

    # ---------- 底层 ----------
    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}/vn"
        last_error: Exception | None = None
        for attempt in range(1, self.max_retry + 1):
            wait = self._last_request + self.interval - time.time()
            if wait > 0:
                time.sleep(wait)
            try:
                self._last_request = time.time()
                resp = self.session.post(url, json=body, timeout=self.timeout, proxies=self.proxies)
            except requests.RequestException as exc:
                last_error = VndbError(f"VNDB 连接失败: {exc}")
                time.sleep(min(10, 2 * attempt))
                continue

            if resp.status_code == 200:
                try:
                    return resp.json()
                except ValueError as exc:
                    raise VndbError("VNDB 返回的不是 JSON") from exc
            if resp.status_code in (429, 500, 502, 503, 504):
                last_error = VndbError(f"VNDB HTTP {resp.status_code}: {resp.text[:200]}")
                time.sleep(min(20, 3 * attempt))
                continue
            raise VndbError(f"VNDB HTTP {resp.status_code}: {resp.text[:300]}")
        raise last_error or VndbError("VNDB 请求失败")

    def fetch_page(self, min_rating: float, page: int = 1, results: int | None = None) -> tuple[list[Vn], bool]:
        """取一页：rating >= min_rating*10 且 olang=ja，rating 降序。"""
        body = {
            "filters": [
                "and",
                ["rating", ">=", int(round(float(min_rating) * 10))],
                ["olang", "=", "ja"],
            ],
            "fields": FIELDS,
            "sort": "rating",
            "reverse": True,
            "results": int(results or self.page_size),
            "page": int(page),
        }
        data = self._post(body)
        items = [_to_vn(it) for it in (data.get("results") or [])]
        # 本地兜底：VNDB 若忽略 olang 条件也不会把非日语原版混进来
        items = [vn for vn in items if not vn.olang or vn.olang == "ja"]
        return items, bool(data.get("more"))

    def iter_vns(self, min_rating: float, max_pages: int | None = None) -> Iterator[Vn]:
        """按分数档翻页迭代。max_pages 用于试跑时限制条数。"""
        page = 1
        while True:
            items, more = self.fetch_page(min_rating, page)
            for vn in items:
                yield vn
            if not more or (max_pages and page >= max_pages):
                return
            page += 1

    def get_vn(self, vndb_id: str) -> Vn | None:
        body = {"filters": ["id", "=", str(vndb_id)], "fields": FIELDS, "results": 1}
        data = self._post(body)
        items = [_to_vn(it) for it in (data.get("results") or [])]
        return items[0] if items else None

    def search(self, text: str, limit: int = 5) -> list[Vn]:
        """按名字搜（VNDB 的 search 会同时匹配标题、别名、罗马字）。"""
        body = {"filters": ["search", "=", str(text or "")], "fields": FIELDS, "results": int(limit)}
        data = self._post(body)
        return [_to_vn(it) for it in (data.get("results") or [])]

    def count(self, min_rating: float) -> int:
        """粗略统计某分数档以上的作品数（只数不取详情，翻页到 more=false）。"""
        total = 0
        page = 1
        while True:
            items, more = self.fetch_page(min_rating, page)
            total += len(items)
            if not more or not items:
                return total
            page += 1
