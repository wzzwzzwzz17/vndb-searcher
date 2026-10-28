# -*- coding: utf-8 -*-
"""ggbase（镜像 ggb.dlgal.com）爬虫：产出磁力链接。

站点特点（实测）：
- 搜索：GET {base}/search.so?title=<关键词>&p=<页码从0开始>
- 结果行：<tr class='dtr' ...> 内含 <a href="//host/view.so?id=NNN">标题</a>
  以及 <span class='labelaG bc2 newf'>10.5G</span> 大小
- 磁力：GET {base}/magnet.so?id=NNN 拿 downid → POST {base}/magnet.so?downid=..&json=1&id=NNN
  返回 {"hash": "..."}，拼成 magnet:?xt=urn:btih:<hash>

原站 www.ggbases.com 在部分网络下不可达，所以 base 列表第一个是镜像。
"""

from __future__ import annotations

import html as html_mod
import re
import time
from typing import Any
from urllib.parse import quote, urlparse

from .. import title_match
from ..logger import get_logger
from .base import SOURCE_MAGNET, BaseCrawler, CrawlerError, Resource, parse_size_gb

_ROW = re.compile(r"<tr[^>]*class=['\"]dtr[^>]*>(.*?)</tr>", re.S | re.I)
_VIEW = re.compile(r"<a[^>]+href=['\"](?:https?:)?//([^'\"]+?/view\.so\?id=(\d+))['\"][^>]*>(.*?)</a>", re.S | re.I)
# 体积单元格的 class 各版本不同（labelaG / labelFK / label5B 都见过），
# 所以只认「纯体积文本」的 span，不依赖 class。
_SIZE = re.compile(r"<span[^>]*>\s*(\d+(?:\.\d+)?\s*[TGMK]B?)\s*</span>", re.S | re.I)
_DATE = re.compile(r"<span[^>]*class=['\"][^'\"]*newf[^'\"]*['\"][^>]*>", re.I)

# 原声碟/广播剧这类非游戏本体：直接丢弃（标题里带游戏名，但内容不是游戏）。
# 不要在这里加 `アレンジ` `同梱特典` 之类的词：正版游戏包标题就长这样，
# 如 `ランス10 -決戦- パッケージ版 + 同梱特典 アレンジコレクション`。
_HARD_NOISE = (
    "サウンドアルバム", "サウンドトラック", "soundtrack", "ボイスドラマ", "ドラマcd",
    "cg集", "壁紙", "カレンダー", "ost",
)


class GgbaseCrawler(BaseCrawler):
    site = "ggbase"
    source_type = SOURCE_MAGNET

    def __init__(self, cfg: dict[str, Any]):
        super().__init__(cfg)
        bases = (cfg.get("crawler") or {}).get("ggbase_bases") or ["https://ggb.dlgal.com"]
        self.bases = [str(b).rstrip("/") for b in bases if str(b).strip()]
        self.base = self.bases[0]
        self.log = get_logger()

    # ---------- 搜索 ----------
    def search(self, names: list[str], threshold: float | None = None) -> list[Resource]:
        limit = self.threshold if threshold is None else float(threshold)
        found: list[Resource] = []
        tried: set[str] = set()
        seen_ids: set[str] = set()

        for term in title_match.search_terms(names):
            if term in tried:
                continue
            tried.add(term)
            try:
                rows = self._search_term(term)
            except CrawlerError as exc:
                self.log.warning("ggbase 搜索 %r 失败: %s", term, exc)
                if self._switch_base():
                    try:
                        rows = self._search_term(term)
                    except CrawlerError:
                        continue
                else:
                    continue

            for title, vid, size_text in rows:
                if vid in seen_ids:
                    continue  # 镜像站翻页参数有时无效，会返回同一批结果
                if _is_noise(title):
                    self.log.debug("ggbase 跳过非游戏本体: %s", title)
                    continue
                score, _ = title_match.best_match(title, names)
                if score < limit:
                    continue
                seen_ids.add(vid)
                detail = f"{self.base}/view.so?id={vid}"
                found.append(Resource(
                    site=self.site,
                    source_type=SOURCE_MAGNET,
                    title=title,
                    link="",  # 磁力链接需要再请求一次才拿得到
                    size_gb=parse_size_gb(size_text),
                    detail_url=detail,
                    match_score=score,
                    extra={"view_id": vid, "size_text": size_text},
                ))
            if found:
                break  # 命中最准的关键词就够了，别把站点刷满

        return self._finish(found, limit)

    def _search_term(self, term: str) -> list[tuple[str, str, str]]:
        """返回 [(标题, view_id, 大小文本)]，跨页收集。"""
        results: list[tuple[str, str, str]] = []
        for page in range(max(1, self.max_pages)):
            url = f"{self.base}/search.so?title={quote(term)}&p={page}"
            html = self.get(url).text
            parsed = parse_rows(html)
            if not parsed:
                break
            for title, host, vid, size_text in parsed:
                # 镜像/原站混排时以实际返回的 host 为准，保证后续请求同源
                if host:
                    self.base = f"https://{host}"
                results.append((title, vid, size_text))
        return results

    def _switch_base(self) -> bool:
        """第一个 base 挂了就换下一个，成功返回 True。"""
        for base in self.bases:
            if base == self.base:
                continue
            self.base = base
            self.log.info("ggbase 切换到 %s", base)
            return True
        return False

    # ---------- 磁力 ----------
    def resolve_magnet(self, res: Resource) -> str:
        """把搜索结果补成完整 magnet 链接。"""
        if res.link:
            return res.link
        vid = str(res.extra.get("view_id") or "")
        if not vid:
            raise CrawlerError("ggbase 资源缺少 view id")
        detail = f"{self.base}/view.so?id={vid}"
        magnet_page = f"{self.base}/magnet.so?id={vid}"
        # downid 只在 magnet.so 页面的内联 JS 里出现（view 页只有按钮，没有 downid），
        # 形如 $.ajax({url: "/magnet.so?downid=<uuid>&json=1&id=86784", ...})
        page = self.get(magnet_page).text
        downid = self._find_downid(page)
        if not downid:
            page = self.get(detail).text
            downid = self._find_downid(page)
        if not downid:
            raise CrawlerError(f"ggbase 磁力页没找到 downid: {magnet_page}")

        url = f"{self.base}/magnet.so?downid={downid}&json=1&id={vid}"
        data: dict[str, Any] = {}
        for attempt in range(1, 3):
            self._throttle()
            resp = self.session.post(
                url,
                headers={
                    "Referer": magnet_page,
                    "X-Requested-With": "XMLHttpRequest",
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                },
                timeout=self.timeout,
                proxies=self.proxies,
            )
            if resp.status_code != 200:
                raise CrawlerError(f"ggbase 取磁力失败 HTTP {resp.status_code}")
            data = self._magnet_payload(resp)
            if data:
                break
            # 连点太快时接口会回一坨空 HTML，等一会儿再试一次
            if attempt == 1:
                time.sleep(2)
        if not data:
            raise CrawlerError("ggbase 磁力接口返回无法解析")

        hash_value = str(data.get("hash") or data.get("magnet") or "").strip()
        if not hash_value:
            raise CrawlerError("ggbase 磁力接口没返回 hash")
        magnet = hash_value if hash_value.startswith("magnet:") else f"magnet:?xt=urn:btih:{hash_value}"
        res.link = magnet
        return magnet

    @staticmethod
    def _magnet_payload(resp: Any) -> dict[str, Any]:
        """从磁力接口的响应里挖出 hash；两种写法都见得到。"""
        try:
            data = resp.json()
        except ValueError:
            data = None
        if isinstance(data, dict) and (data.get("hash") or data.get("magnet")):
            return data
        text = resp.text or ""
        match = re.search(r'"(?:hash|magnet)"\s*:\s*"([0-9a-fA-F]{32,40}|magnet:[^"]+)"', text)
        if match:
            return {"hash": match.group(1)}
        match = re.search(r"btih:([0-9a-fA-F]{32,40})", text)
        return {"hash": match.group(1)} if match else {}

    @staticmethod
    def _find_downid(page: str) -> str:
        """从页面里挖 downid；页面可能自己生成一个 uuid，所以只认带连字符的 uuid 形式。"""
        for match in re.finditer(r"downid=([0-9a-fA-F\-]{8,})", page):
            value = match.group(1)
            if "-" in value:
                return value
        match = re.search(r"downid['\"]?\s*[:=]\s*['\"]?([0-9a-fA-F][0-9a-fA-F\-]{7,})", page, re.I)
        return match.group(1) if match else ""


def parse_rows(html: str) -> list[tuple[str, str, str, str]]:
    """解析搜索结果页，返回 [(标题, host, view_id, 大小文本)]。"""
    out: list[tuple[str, str, str, str]] = []
    for row in _ROW.findall(html):
        view = _VIEW.search(row)
        if not view:
            continue
        host, vid, title_html = view.group(1), view.group(2), view.group(3)
        size_match = _SIZE.search(row)
        out.append((
            _clean(title_html),
            host.split("/")[0],
            vid,
            _clean(size_match.group(1)) if size_match else "",
        ))
    return out


def _is_noise(title: str) -> bool:
    """原声碟/广播剧/壁纸集之类的条目：名字里带游戏名，但根本不是游戏本体。

    只匹配「硬噪声」。`アレンジ` `同梱特典` 这类词在正版游戏包里也常出现
    （如 `ランス10 -決戦- パッケージ版 + 同梱特典 アレンジコレクション`），
    加进来会把真本体误杀，所以这里刻意不收。
    """
    low = title.lower()
    return any(word in low for word in _HARD_NOISE)


def _clean(html: str) -> str:
    text = re.sub(r"<[^>]+>", "", html)
    text = html_mod.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def host_of(url: str) -> str:
    return urlparse(url).netloc
