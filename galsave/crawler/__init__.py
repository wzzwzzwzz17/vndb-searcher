# -*- coding: utf-8 -*-
"""爬虫集合：按 source_priority 顺序查 A/B/C 三类资源。"""

from __future__ import annotations

from typing import Any

from ..logger import get_logger
from .base import SOURCE_DIRECT, SOURCE_MAGNET, SOURCE_SHARE, BaseCrawler, CrawlerError, Resource, parse_size_gb
from .ggbase import GgbaseCrawler
from .kungal import KungalCrawler
from .shinnku import ShinnkuCrawler

__all__ = [
    "BaseCrawler",
    "CrawlerError",
    "Resource",
    "parse_size_gb",
    "GgbaseCrawler",
    "KungalCrawler",
    "ShinnkuCrawler",
    "build_crawlers",
    "SearchEngine",
    "SOURCE_MAGNET",
    "SOURCE_SHARE",
    "SOURCE_DIRECT",
]


def build_crawlers(cfg: dict[str, Any]) -> list[BaseCrawler]:
    """实例化全部爬虫；单个站点初始化失败不影响其它站。"""
    log = get_logger()
    crawlers: list[BaseCrawler] = []
    for cls in (GgbaseCrawler, KungalCrawler, ShinnkuCrawler):
        try:
            crawlers.append(cls(cfg))
        except Exception as exc:  # noqa: BLE001 - 单站异常不该拖垮整个流程
            log.warning("初始化 %s 失败: %s", cls.__name__, exc)
    return crawlers


class SearchEngine:
    """把一部作品丢给所有站点，按 source_priority 排序返回候选资源。"""

    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg
        self.log = get_logger()
        self.crawlers = build_crawlers(cfg)
        self.priority: list[str] = list(cfg.get("source_priority") or ["magnet", "share", "direct"])

    def sites(self) -> list[str]:
        return [crawler.site for crawler in self.crawlers]

    def find(self, names: list[str], allow_types: list[str] | None = None) -> list[Resource]:
        """查全部站点，返回按「来源优先级 → 匹配分 → 体积」排序的结果。"""
        allowed = set(allow_types) if allow_types else set(self.priority)
        results: list[Resource] = []
        for crawler in self.crawlers:
            if crawler.source_type not in allowed:
                continue
            try:
                found = crawler.search(names)
            except CrawlerError as exc:
                self.log.warning("%s 搜索失败: %s", crawler.site, exc)
                continue
            except Exception as exc:  # noqa: BLE001 - 站点改版不应该崩掉整轮
                self.log.warning("%s 搜索异常: %s", crawler.site, exc)
                continue
            if found:
                self.log.info("%s 命中 %d 条（%s）", crawler.site, len(found), names[0] if names else "")
            results.extend(found)

        order = {name: idx for idx, name in enumerate(self.priority)}
        results.sort(key=lambda r: (
            order.get(r.source_type, 99),
            -r.match_score,
            -r.size_gb,
        ))
        return results

    def pick(self, names: list[str], allow_types: list[str] | None = None) -> Resource | None:
        """取最合适的一条（来源优先级最高、匹配最好、体积最大）。"""
        found = self.find(names, allow_types)
        return found[0] if found else None

    def resolve(self, res: Resource) -> Resource:
        """把搜索结果补成可用的链接（磁力 / 直链）。"""
        for crawler in self.crawlers:
            if crawler.site != res.site:
                continue
            if res.source_type == SOURCE_MAGNET and hasattr(crawler, "resolve_magnet"):
                crawler.resolve_magnet(res)  # type: ignore[attr-defined]
            elif res.source_type == SOURCE_DIRECT and hasattr(crawler, "resolve_direct"):
                crawler.resolve_direct(res)  # type: ignore[attr-defined]
            return res
        raise CrawlerError(f"没有能解析 {res.site} 资源的爬虫")
