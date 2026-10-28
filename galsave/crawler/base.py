# -*- coding: utf-8 -*-
"""三站爬虫的统一接口。

每个源产出的 Resource 只描述「资源在哪、怎么拿」，不负责真去下载；
由 cloud.py / downloader 决定走 A/B/C 哪条路。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

import requests

from ..logger import get_logger

SOURCE_MAGNET = "magnet"   # A：磁力 → 百度离线下载
SOURCE_SHARE = "share"     # B：网盘分享 → 云端转存
SOURCE_DIRECT = "direct"   # C：直链 → 本地下载 → 加密 → 上传

_SIZE_PATTERNS = (
    (re.compile(r"([\d.]+)\s*TB?\b", re.I), 1000.0),
    (re.compile(r"([\d.]+)\s*GB?\b", re.I), 1.0),
    (re.compile(r"([\d.]+)\s*MB?\b", re.I), 0.001),
    (re.compile(r"([\d.]+)\s*KB?\b", re.I), 0.000001),
)


def parse_size_gb(text: str) -> float:
    """把 `10.5G` / `872.11 MB` / `1.2 TB` 解析成 GB。"""
    raw = str(text or "")
    for pattern, factor in _SIZE_PATTERNS:
        match = pattern.search(raw)
        if match:
            try:
                return round(float(match.group(1)) * factor, 3)
            except ValueError:
                continue
    return 0.0


@dataclass
class Resource:
    """一条候选资源。"""

    site: str
    source_type: str
    title: str
    link: str
    size_gb: float = 0.0
    detail_url: str = ""
    match_score: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return f"[{self.site}/{self.source_type}] {self.title} ({self.size_gb:.2f}GB) {self.link[:80]}"


class CrawlerError(RuntimeError):
    """站点结构变了或请求失败。"""


class BaseCrawler:
    """所有爬虫的公共部分：会话、节流、重试。"""

    site = "base"
    source_type = SOURCE_MAGNET

    def __init__(self, cfg: dict[str, Any]):
        crawler_cfg = cfg.get("crawler") or {}
        self.cfg = cfg
        self.threshold = float(cfg.get("fuzzy_match_threshold") or 85)
        self.timeout = float(crawler_cfg.get("timeout") or 25)
        self.interval = float(crawler_cfg.get("interval") or 1.0)
        self.max_pages = int(crawler_cfg.get("max_pages") or 2)
        proxy = str(crawler_cfg.get("proxy") or "").strip()
        self.proxies = {"http": proxy, "https": proxy} if proxy else None
        self.log = get_logger()
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
            ),
            "Accept-Language": "ja,en;q=0.8,zh-CN;q=0.6",
        })
        self._last_request = 0.0

    # ---------- 工具 ----------
    def _throttle(self) -> None:
        wait = self._last_request + self.interval - time.time()
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.time()

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        """带节流和重试的 GET。"""
        last_exc: Exception | None = None
        for attempt in range(1, 4):
            self._throttle()
            try:
                resp = self.session.get(url, timeout=self.timeout, proxies=self.proxies, **kwargs)
                if resp.status_code == 200:
                    return resp
                if resp.status_code in (429, 500, 502, 503, 504):
                    last_exc = CrawlerError(f"{self.site} HTTP {resp.status_code}: {url}")
                    time.sleep(min(15, 2 * attempt))
                    continue
                raise CrawlerError(f"{self.site} HTTP {resp.status_code}: {url}")
            except requests.RequestException as exc:
                last_exc = CrawlerError(f"{self.site} 连接失败: {exc}")
                time.sleep(min(15, 2 * attempt))
        raise last_exc or CrawlerError(f"{self.site} 请求失败: {url}")

    def get_json(self, url: str, **kwargs: Any) -> Any:
        resp = self.get(url, **kwargs)
        try:
            return resp.json()
        except ValueError as exc:
            raise CrawlerError(f"{self.site} 返回的不是 JSON: {url}") from exc

    def post_json(self, url: str, payload: Any = None, **kwargs: Any) -> Any:
        """带节流和重试的 POST（有些接口要用 POST 才给真链接）。"""
        last_exc: Exception | None = None
        for attempt in range(1, 4):
            self._throttle()
            try:
                resp = self.session.post(url, json=payload if payload is not None else {},
                                         timeout=self.timeout, proxies=self.proxies, **kwargs)
                if resp.status_code == 200:
                    try:
                        return resp.json()
                    except ValueError as exc:
                        raise CrawlerError(f"{self.site} 返回的不是 JSON: {url}") from exc
                if resp.status_code in (429, 500, 502, 503, 504):
                    last_exc = CrawlerError(f"{self.site} HTTP {resp.status_code}: {url}")
                    time.sleep(min(15, 2 * attempt))
                    continue
                raise CrawlerError(f"{self.site} HTTP {resp.status_code}: {url}")
            except requests.RequestException as exc:
                last_exc = CrawlerError(f"{self.site} 连接失败: {exc}")
                time.sleep(min(15, 2 * attempt))
        raise last_exc or CrawlerError(f"{self.site} 请求失败: {url}")

    # ---------- 子类实现 ----------
    def search(self, names: list[str], threshold: float | None = None) -> list[Resource]:
        """按一批候选名搜索，返回已过匹配阈值的资源（按分数、大小降序）。"""
        raise NotImplementedError

    def _finish(self, resources: list[Resource], threshold: float | None = None) -> list[Resource]:
        """过滤低分、去重、排序。

        注意：磁力/直链类资源的 `link` 要等 `resolve_*` 才会填上，
        所以去重键取 `link or detail_url`，不能要求 `link` 非空。
        另外同一站点常有多个重复上传（标题、体积都一样，view id 不同），
        再按「站点+标题+体积」去一次，避免重复下载同一份内容。
        """
        limit = self.threshold if threshold is None else float(threshold)
        kept: list[Resource] = []
        seen: set[str] = set()
        seen_titles: set[tuple[str, str, float]] = set()
        for res in resources:
            key = res.link or res.detail_url
            if not key or key in seen:
                continue
            seen.add(key)
            title_key = (res.site, res.title.lower(), round(res.size_gb, 1))
            if title_key in seen_titles:
                continue
            seen_titles.add(title_key)
            if res.match_score >= limit:
                kept.append(res)
        kept.sort(key=lambda r: (r.match_score, r.size_gb), reverse=True)
        return kept
