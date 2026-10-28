# -*- coding: utf-8 -*-
"""真红 shinnku 爬虫：产出直链（走 C：本地下载 → AES-GCM 加密 → 上传百度）。

站点结构（实测，Next.js SSR，没有 JSON API）：
- 搜索：GET {base}/search?q=<名>
  结果卡片里 <a class="text-lg text-blue-600 hover:underline" href="/files/shinnku/zd/...">文件名</a>
  卡片文本里带体积，如「熟肉 872.11 MB」
- 详情页：GET {base}/files/... 里面给直链，形如
  https://zd.shinnku.top/file/shinnku/zd/...
  合集区（`galgame0/`）的直链主机是 https://galgamedownload.date/...
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote, unquote

from .. import title_match
from ..logger import get_logger
from .base import SOURCE_DIRECT, BaseCrawler, CrawlerError, Resource, parse_size_gb

_CARD = re.compile(
    r"<a[^>]+class=['\"][^'\"]*text-lg[^'\"]*text-blue-600[^'\"]*['\"][^>]*"
    r"href=['\"](/files/[^'\"]+)['\"][^>]*>(.*?)</a>",
    re.S | re.I,
)
_ANY_FILE_HREF = re.compile(r"href=['\"](/files/[^'\"]+)['\"][^>]*>(.*?)</a>", re.S | re.I)
_SIZE = re.compile(r"(\d+(?:\.\d+)?\s*(?:TB|GB|MB|KB))", re.I)
_DIRECT = re.compile(r"https?://[a-z0-9.\-]*shinnku[a-z0-9.\-]*/file/[^\"'<>\s]+", re.I)
_ANCHOR = re.compile(r"href=['\"](https?://[^'\"]+)['\"]", re.I)
_FILE_EXT = re.compile(r"\.(?:rar|zip|7z|iso|tar|gz|tgz|part\d*\.rar)$", re.I)
# 实测的下载主机：zd CDN 与合集区的 galgamedownload.date
_DOWNLOAD_HOSTS = ("zd.shinnku.top", "galgamedownload.date")

# 原声碟/广播剧这类非游戏本体：直接丢弃（标题里带游戏名，但内容不是游戏）。
# 不收 `アレンジ` `同梱特典`：正版游戏包标题就带这些词。
_HARD_NOISE = (
    "サウンドアルバム", "サウンドトラック", "soundtrack", "ボイスドラマ", "ドラマcd",
    "cg集", "壁紙", "カレンダー", "ost",
)


class ShinnkuCrawler(BaseCrawler):
    site = "shinnku"
    source_type = SOURCE_DIRECT

    def __init__(self, cfg: dict[str, Any]):
        super().__init__(cfg)
        self.base = str((cfg.get("crawler") or {}).get("shinnku_base") or "https://www.shinnku.com").rstrip("/")
        self.log = get_logger()

    def search(self, names: list[str], threshold: float | None = None) -> list[Resource]:
        limit = self.threshold if threshold is None else float(threshold)
        found: list[Resource] = []
        seen_paths: set[str] = set()
        for term in title_match.search_terms(names):
            try:
                cards = self._search_term(term)
            except CrawlerError as exc:
                self.log.warning("shinnku 搜索 %r 失败: %s", term, exc)
                continue
            for title, path, size_text in cards:
                if path in seen_paths:
                    continue
                if _is_noise(title):
                    self.log.debug("shinnku 跳过非游戏本体: %s", title)
                    continue
                score, _ = title_match.best_match(title, names)
                if score < limit:
                    continue
                seen_paths.add(path)
                detail = f"{self.base}{path}"
                found.append(Resource(
                    site=self.site,
                    source_type=SOURCE_DIRECT,
                    title=title,
                    link="",  # 直链要进详情页才拿得到
                    size_gb=parse_size_gb(size_text),
                    detail_url=detail,
                    match_score=score,
                    extra={"path": path, "size_text": size_text},
                ))
            if found:
                break
        return self._finish(found, limit)

    def _search_term(self, term: str) -> list[tuple[str, str, str]]:
        url = f"{self.base}/search?q={quote(term)}"
        html = self.get(url).text
        return parse_cards(html)

    def resolve_direct(self, res: Resource) -> str:
        """进详情页拿真实直链。

        两种存储布局：
        - `shinnku/zd/...` 走 CDN `https://zd.shinnku.top/file/...`
        - `galgame0/...`（合集区）走 `https://galgamedownload.date/...`
        所以不能只认 `/file/` 一种形态。
        """
        if res.link:
            return res.link
        detail = res.detail_url or f"{self.base}{res.extra.get('path', '')}"
        if not detail:
            raise CrawlerError("shinnku 资源缺少详情页地址")
        html = self.get(detail).text
        links = pick_direct_links(html, detail)
        if not links:
            raise CrawlerError(f"shinnku 详情页没找到直链: {detail}")
        res.link = links[0]
        return res.link


def pick_direct_links(html: str, detail: str) -> list[str]:
    """从详情页 HTML 里挑出真正的下载直链，按可信度排序（纯函数，便于测试）。

    详情页里有导航链接（`/files/shinnku/zd` 之类），必须靠「主机白名单 + 文件名一致」
    把真正的下载地址挑出来：
    1. 下载主机白名单里的、且文件名与详情页一致的；
    2. 白名单里的任意文件链接；
    3. 文件名一致的绝对文件链接。
    """
    detail_clean = detail.split("?")[0]
    detail_name = unquote(detail_clean.rsplit("/", 1)[-1]).lower()
    ranked: list[tuple[int, int, int, str]] = []
    for href in set(_ANCHOR.findall(html)):
        if href.split("?")[0] == detail_clean:
            continue
        name = unquote(href.split("?")[0].rsplit("/", 1)[-1]).lower()
        on_host = any(host in href for host in _DOWNLOAD_HOSTS)
        same_name = bool(detail_name) and name == detail_name
        if not (on_host or (same_name and _FILE_EXT.search(name))):
            continue
        if on_host and same_name:
            rank = 0
        elif on_host:
            rank = 1
        else:
            rank = 2
        ranked.append((rank, 0 if "zd." in href else 1, len(href), href))
    ranked.sort()
    links = [item[3] for item in ranked]
    if not links:
        # 兜底：老版页面把直链直接写在正文里
        links = [u for u in _DIRECT.findall(html) if u.split("?")[0] != detail_clean]
    return links


def parse_cards(html: str) -> list[tuple[str, str, str]]:
    """解析搜索结果页，返回 [(标题, /files/路径, 大小文本)]。"""
    cards = _CARD.findall(html) or _ANY_FILE_HREF.findall(html)
    out: list[tuple[str, str, str]] = []
    for path, title_html in cards:
        title = _clean(title_html)
        if not title:
            continue
        # 体积在卡片附近的文本里，取标题结束位置之后 400 字符内第一个体积
        idx = html.find(title_html)
        tail = html[idx + len(title_html): idx + len(title_html) + 400] if idx >= 0 else ""
        size_match = _SIZE.search(tail)
        out.append((title, path, size_match.group(1) if size_match else ""))
    return out


def _is_noise(title: str) -> bool:
    """原声碟、广播剧之类的条目：名字里带游戏名，但不是游戏本体。"""
    low = title.lower()
    return any(word in low for word in _HARD_NOISE)


def _clean(html: str) -> str:
    import html as html_mod

    text = re.sub(r"<[^>]+>", "", html)
    text = html_mod.unescape(text)
    return re.sub(r"\s+", " ", text).strip()
