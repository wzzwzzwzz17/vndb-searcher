# -*- coding: utf-8 -*-
"""鲲 kungal 爬虫：产出百度网盘分享链接（走 B 转存，不占本地流量）。

站点 API（实测）：
- 搜索：GET {base}/api/v1/search/works?q=<名>&page=1&limit=10
  → items[{id, display_name, localized{en:{value}, ja:{value}, zh-Hans:{value}}}]
- 资源：GET {base}/api/v1/works/{id}/resources
  → items[{id, resource_type, title, size "11.11 GB", provider_names ["百度网盘"],
            content 文档树}]
  百度链接藏在 content 的 link 节点里，形如
  https://pan.baidu.com/s/1Dau8DVO5_HcMjOD4nFA4Qg?pwd=t7cp

content 里偶尔混着「模拟器及解压工具」之类的网盘链接，按关键词排除；
collection（合集）默认跳过，避免一次拖几百 GB。
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

from .. import title_match
from ..logger import get_logger
from .base import SOURCE_SHARE, BaseCrawler, CrawlerError, Resource, parse_size_gb

_BAIDU = re.compile(r"https?://(?:pan|yun)\.baidu\.com/s/[\w\-]+(?:\?pwd=[\w]+)?")
_TOOL_WORDS = ("模拟器", "解压工具", "教程", "工具包", "汉化补丁", "字体包")


class KungalCrawler(BaseCrawler):
    site = "kungal"
    source_type = SOURCE_SHARE

    def __init__(self, cfg: dict[str, Any]):
        super().__init__(cfg)
        self.base = str((cfg.get("crawler") or {}).get("kungal_base") or "https://www.kungal.com").rstrip("/")
        self.allow_collection = bool((cfg.get("crawler") or {}).get("allow_collection"))
        self.log = get_logger()

    # ---------- 搜索 ----------
    def search(self, names: list[str], threshold: float | None = None) -> list[Resource]:
        limit = self.threshold if threshold is None else float(threshold)
        found: list[Resource] = []
        seen_works: set[str] = set()
        for term in title_match.search_terms(names):
            try:
                works = self._search_works(term)
            except CrawlerError as exc:
                self.log.warning("kungal 搜索 %r 失败: %s", term, exc)
                continue

            hit = None
            for work in works:
                work_id = str(work.get("id") or "")
                if work_id in seen_works:
                    continue
                score = self._score_work(work, names)
                if score >= limit:
                    hit = (work, score)
                    break
            if not hit:
                continue
            work, score = hit
            seen_works.add(str(work.get("id") or ""))
            try:
                found.extend(self._resources_of(work, score, names))
            except CrawlerError as exc:
                self.log.warning("kungal 取作品 %s 资源失败: %s", work.get("id"), exc)
            if found:
                break
        return self._finish(found, limit)

    def _search_works(self, term: str) -> list[dict[str, Any]]:
        url = f"{self.base}/api/v1/search/works?q={quote(term)}&page=1&limit=10"
        data = self.get_json(url, headers={"Accept": "application/json"})
        items = []
        if isinstance(data, dict):
            payload = data.get("data") if isinstance(data.get("data"), dict) else data
            items = payload.get("items") or []
        return [it for it in items if isinstance(it, dict)]

    @staticmethod
    def _work_names(work: dict[str, Any]) -> list[str]:
        names = [str(work.get("display_name") or "")]
        localized = work.get("localized") or {}
        if isinstance(localized, dict):
            for value in localized.values():
                if isinstance(value, dict):
                    names.append(str(value.get("value") or ""))
                elif isinstance(value, str):
                    names.append(value)
        return [n for n in names if n.strip()]

    def _score_work(self, work: dict[str, Any], names: list[str]) -> float:
        work_names = self._work_names(work)
        if not work_names:
            return 0.0
        best = 0.0
        for mine in names:
            for theirs in work_names:
                best = max(best, title_match.similarity(mine, theirs))
        return round(best, 2)

    # ---------- 资源 ----------
    def _resources_of(self, work: dict[str, Any], score: float, names: list[str]) -> list[Resource]:
        work_id = str(work.get("id") or "")
        if not work_id:
            return []
        url = f"{self.base}/api/v1/works/{work_id}/resources"
        data = self.get_json(url, headers={"Accept": "application/json"})
        payload = data.get("data") if isinstance(data, dict) and isinstance(data.get("data"), dict) else data
        items = (payload or {}).get("items") or []
        return self._resources_from_items(items, work, score, names)

    def _resources_from_items(
        self,
        items: Any,
        work: dict[str, Any],
        score: float,
        names: list[str],
    ) -> list[Resource]:
        """把资源 API 的 items 转成 Resource（纯函数，便于离线测试）。"""
        work_id = str(work.get("id") or "")
        out: list[Resource] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            res_type = str(item.get("resource_type") or "game")
            if res_type == "collection" and not self.allow_collection:
                # 合集动辄上百 GB，默认跳过；标题常为空，日志里用 id 兜底
                self.log.debug("kungal 跳过合集: %s", item.get("title") or item.get("id"))
                continue
            title = str(item.get("title") or work.get("display_name") or "")
            if any(word in title for word in _TOOL_WORDS):
                continue
            providers = [str(p) for p in (item.get("provider_names") or [])]
            links = _collect_links(item.get("content"))
            if not links:
                continue
            # 优先百度网盘；只有明确标了别的盘才跳过
            link = links[0]
            if providers and not any("百度" in p for p in providers):
                continue
            pwd = ""
            match = re.search(r"[?&]pwd=([\w]+)", link)
            if match:
                pwd = match.group(1)
            out.append(Resource(
                site=self.site,
                source_type=SOURCE_SHARE,
                title=f"{title} [{work.get('display_name') or ''}]".strip(),
                link=link,
                size_gb=parse_size_gb(str(item.get("size") or "")),
                detail_url=f"{self.base}/works/{work_id}/resources",
                match_score=score,
                extra={
                    "work_id": work_id,
                    "resource_id": item.get("id"),
                    "pwd": pwd,
                    "providers": providers,
                    "resource_type": res_type,
                    "names": names,
                },
            ))
        return out


def _collect_links(node: Any, depth: int = 0) -> list[str]:
    """递归挖 content 文档树里的百度链接。"""
    if depth > 12:
        return []
    found: list[str] = []
    if isinstance(node, dict):
        kind = str(node.get("type") or "")
        if kind == "link":
            for key in ("url", "href", "link", "text"):
                value = node.get(key)
                if isinstance(value, str):
                    found.extend(_BAIDU.findall(value))
        for key, value in node.items():
            if key in {"text", "url", "href", "link"} and isinstance(value, str):
                found.extend(_BAIDU.findall(value))
            elif isinstance(value, (dict, list)):
                found.extend(_collect_links(value, depth + 1))
    elif isinstance(node, list):
        for item in node:
            found.extend(_collect_links(item, depth + 1))
    elif isinstance(node, str):
        found.extend(_BAIDU.findall(node))
    # 去重保序
    out: list[str] = []
    for link in found:
        link = link.rstrip(".,;)'\"、。，）")
        if link not in out:
            out.append(link)
    return out
