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
# 正文里抠解压码（不是百度 ?pwd= 提取码）
_ARCHIVE_PASS = (
    re.compile(
        r"(?:解压(?:密码|碼|码)|壓縮密碼|压缩密码|archive\s*password)\s*[：:=\s]\s*"
        r"[`「『\"']?([^\s`」』\"'，,；;|/]{1,64})[`」』\"']?",
        re.I,
    ),
    re.compile(
        r"(?:解压码|解壓碼)\s*[：:]\s*[`「『\"']?([A-Za-z0-9_\-]{1,64})[`」』\"']?",
        re.I,
    ),
)


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
            state = str(item.get("state") or "").lower()
            if state and state != "valid":
                # 站点自己会标 expired（分享失效），没必要再浪费一次解析
                self.log.debug("kungal 跳过失效资源: %s (%s)", item.get("id"), state)
                continue
            title = str(item.get("title") or work.get("display_name") or "")
            if any(word in title for word in _TOOL_WORDS):
                continue
            providers = [str(p) for p in (item.get("provider_names") or [])]
            if providers and not any("百度" in p for p in providers):
                continue
            # content 里的链接经常只是「模拟器及解压工具」那个公共分享，
            # 真正的下载地址要 POST /api/v1/galgame-resources/{id}/downloads 才给，
            # 所以这里只负责产出候选，链接留到 resolve_share() 再补。
            link = ""
            for candidate in _collect_links(item.get("content")):
                link = candidate
                break
            pwd = ""
            match = re.search(r"[?&]pwd=([\w]+)", link)
            if match:
                pwd = match.group(1)
            resource_id = str(item.get("id") or "")
            display = str(work.get("display_name") or "")
            # 条目自己没写标题时就直接用作品名，别拼成「千恋＊万花 [千恋＊万花]」
            if not display or display in title:
                full_title = title
            elif not title:
                full_title = display
            else:
                full_title = f"{title} [{display}]"
            body_text = _node_text(item.get("content"))
            archive_hint = _find_archive_password(body_text)
            # 接口字段偶发直接带在资源条目上
            for key in ("archive_password", "unzip_password", "password"):
                raw = item.get(key)
                if isinstance(raw, str) and raw.strip():
                    archive_hint = archive_hint or raw.strip()
            out.append(Resource(
                site=self.site,
                source_type=SOURCE_SHARE,
                title=full_title.strip(),
                link=link,
                size_gb=parse_size_gb(str(item.get("size") or "")),
                detail_url=(f"{self.base}/galgame/resource/{resource_id}" if resource_id
                            else f"{self.base}/works/{work_id}/resources"),
                match_score=score,
                extra={
                    "work_id": work_id,
                    "resource_id": resource_id,
                    "pwd": pwd,
                    "archive_password": archive_hint,
                    "providers": providers,
                    "resource_type": res_type,
                    "names": names,
                },
            ))
        return out

    # ---------- 真实下载地址 ----------
    def resolve_share(self, res: Resource) -> Resource:
        """调鲲的 downloads 接口拿真正的下载地址（content 里那个多半是工具分享）。

        实测：POST {base}/api/v1/galgame-resources/{id}/downloads
        → {"object":"galgame_resource_download",
           "download_urls":["https://pan.baidu.com/s/1N7Zv8W...?pwd=rybj"],
           "extraction_code":"rybj","archive_password":""}
        不需要登录，但每点一次都可能被限频，所以只在真要尝试时才调。
        """
        resource_id = str((res.extra or {}).get("resource_id") or "")
        if not resource_id:
            if res.link:
                return res
            raise CrawlerError("kungal 资源缺少 id")
        url = f"{self.base}/api/v1/galgame-resources/{resource_id}/downloads"
        data = self.post_json(url, {}, headers={"Accept": "application/json"})
        payload = data if isinstance(data, dict) else {}
        urls = payload.get("download_urls") or []
        if isinstance(urls, str):
            urls = [urls]
        baidu = [str(u) for u in urls if "baidu.com" in str(u)]
        if not baidu:
            raise CrawlerError(f"kungal 条目 {resource_id} 没有百度链接（{len(urls)} 个其它链接）")
        res.link = baidu[0]
        extra = dict(res.extra or {})
        extra["pwd"] = (
            str(payload.get("extraction_code") or "").strip()
            or str(extra.get("pwd") or "").strip()
        )
        # 解压码：接口优先，其次搜索阶段从正文抠到的
        api_pass = str(
            payload.get("archive_password")
            or payload.get("unzip_password")
            or payload.get("password")
            or ""
        ).strip()
        extra["archive_password"] = api_pass or str(extra.get("archive_password") or "").strip()
        res.extra = extra
        if not res.size_gb:
            res.size_gb = parse_size_gb(str(payload.get("size") or ""))
        return res


def _find_archive_password(text: str) -> str:
    """从资源详情正文里找解压码；找不到返回空串。"""
    raw = str(text or "")
    if not raw.strip():
        return ""
    for pattern in _ARCHIVE_PASS:
        match = pattern.search(raw)
        if match:
            code = match.group(1).strip().strip("`「『」』\"'")
            # 排除明显是百度提取码占位或整段 URL
            if code and "http" not in code.lower() and code.lower() not in {"pwd", "password"}:
                return code[:64]
    return ""


def _collect_links(node: Any, depth: int = 0, context: str = "") -> list[str]:
    """递归挖 content 文档树里的百度链接。

    链接会带上「所在段落」的文本当上下文：鲲的很多条目在正文里写
    「模拟器及解压工具：<链接>」，那个链接不是游戏本体，得靠上下文排除。
    """
    if depth > 12:
        return []
    found: list[str] = []
    if isinstance(node, dict):
        kind = str(node.get("type") or "") or str(node.get("object") or "")
        if kind in {"paragraph", "heading", "blockquote", "list_item", "list"}:
            context = _node_text(node)
        if kind == "link":
            for key in ("url", "href", "link", "text"):
                value = node.get(key)
                if isinstance(value, str) and not _is_tool_context(context + " " + value):
                    found.extend(_BAIDU.findall(value))
        for key, value in node.items():
            if key in {"text", "url", "href", "link"} and isinstance(value, str):
                for link in _BAIDU.findall(value):
                    if not _is_tool_context(context):
                        found.append(link)
            elif isinstance(value, (dict, list)):
                found.extend(_collect_links(value, depth + 1, context))
    elif isinstance(node, list):
        for item in node:
            found.extend(_collect_links(item, depth + 1, context))
    elif isinstance(node, str):
        for link in _BAIDU.findall(node):
            if not _is_tool_context(context):
                found.append(link)
    # 去重保序
    out: list[str] = []
    for link in found:
        link = link.rstrip(".,;)'\"、。，）")
        if link not in out:
            out.append(link)
    return out


def _node_text(node: Any, depth: int = 0) -> str:
    """把一个段落/块里的所有文本拼起来（最多 400 字）。"""
    if depth > 8:
        return ""
    parts: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key in {"value", "text"} and isinstance(value, str):
                parts.append(value)
            elif isinstance(value, (dict, list)):
                parts.append(_node_text(value, depth + 1))
    elif isinstance(node, list):
        for item in node:
            parts.append(_node_text(item, depth + 1))
    elif isinstance(node, str):
        parts.append(node)
    return " ".join(p for p in parts if p)[:400]


def _is_tool_context(text: str) -> bool:
    return any(word in text.lower() for word in _TOOL_WORDS)
