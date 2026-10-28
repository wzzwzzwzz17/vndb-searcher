# -*- coding: utf-8 -*-
"""三源落地：把爬虫找到的资源真的存进自己的百度网盘。

A 磁力 → 离线下载（云端下，不占本地流量；需要 SVIP）
B 分享 → 云端转存（不占本地流量）
C 直链 → 本地下载 → AES-GCM 加密改 .png → 分片上传（占校园流量，走批次配额）

三条路都以「网盘里实际占多大」为入库体积，不用站点标称值。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from baidu_cloud import BaiduClient, BaiduError, OfflineDownloader, ShareTransfer, Uploader
from baidu_cloud.offline import MAGNET_UNSUPPORTED, MAGNET_UNSUPPORTED_ERRNO
from baidu_cloud.size import measure

from .config import cget
from .crawler import Resource
from .crawler.base import SOURCE_DIRECT, SOURCE_MAGNET, SOURCE_SHARE
from .crypto.enc import CryptoError, encrypt_file
from .downloader.direct import DirectDownloader, DownloadError
from .logger import get_logger


@dataclass
class SaveResult:
    ok: bool
    route: str
    site: str = ""
    cloud_path: str = ""
    size_gb: float = 0.0
    detail: str = ""
    error: str = ""

    def __str__(self) -> str:
        if self.ok:
            return f"OK[{self.route}] {self.cloud_path} ({self.size_gb:.2f}GB)"
        return f"FAIL[{self.route}] {self.error}"


class CloudSaver:
    def __init__(self, cfg: dict[str, Any], db: Any, client: BaiduClient | None = None):
        self.cfg = cfg
        self.db = db
        self.log = get_logger()
        bcfg = cfg.get("baidu") or {}
        self.root_dir = str(bcfg.get("root_dir") or "/gal_save")
        self.offline_dir = str(bcfg.get("offline_dir") or f"{self.root_dir}/_offline")
        self.share_dir = str(bcfg.get("share_dir") or f"{self.root_dir}/_share")
        self.encrypted_dir = str(bcfg.get("encrypted_dir") or f"{self.root_dir}/_encrypted")
        self.offline_enabled = bool(bcfg.get("offline_enabled", True))
        self.offline_wait_hours = float(bcfg.get("offline_wait_hours") or 6)
        self.poll_seconds = float(bcfg.get("poll_seconds") or 20)
        self.interval = float(bcfg.get("interval") or 1.5)
        self.c_quota_gb = float(cfg.get("c_quota_per_batch_gb") or 500)
        self.min_gb = float(cfg.get("min_resource_gb") or 0.3)
        self.max_gb = float(cfg.get("max_resource_gb") or 60)

        enc = cfg.get("encrypt") or {}
        self.encrypt_enabled = bool(enc.get("enabled", True))
        self.password = str(enc.get("password") or "")

        self._client = client
        self._downloader = DirectDownloader(cfg)

    # ---------- 懒加载 ----------
    @property
    def client(self) -> BaiduClient:
        if self._client is None:
            self._client = BaiduClient()
            self._client.require_login()
        return self._client

    def c_remaining_gb(self) -> float:
        return max(0.0, self.c_quota_gb - float(self.db.c_quota_used_gb()))

    def save(self, res: Resource) -> SaveResult:
        """按资源类型分派到对应路线。"""
        if res.size_gb and not (self.min_gb <= res.size_gb <= self.max_gb):
            return SaveResult(False, res.source_type, res.site,
                              error=f"体积 {res.size_gb:.2f}GB 不在 {self.min_gb}-{self.max_gb}GB 范围")
        title = _title_for(res)
        if res.source_type == SOURCE_MAGNET:
            return self.save_magnet(res.link, title, res.site)
        if res.source_type == SOURCE_SHARE:
            return self.save_share(res.link, title, res.site)
        if res.source_type == SOURCE_DIRECT:
            return self.save_direct(res.link, title, res.detail_url, res.site)
        return SaveResult(False, res.source_type, res.site, error=f"未知来源类型 {res.source_type}")

    # ---------- A：磁力离线 ----------
    def save_magnet(self, magnet: str, title: str, site: str = "ggbase") -> SaveResult:
        if not self.offline_enabled:
            return SaveResult(False, "magnet", site, error="配置里关了离线下载")
        if not magnet.startswith("magnet:"):
            return SaveResult(False, "magnet", site, error=f"不是磁力链接: {magnet[:40]}")
        try:
            offline = OfflineDownloader(self.client, str((self.cfg.get("baidu") or {}).get("app_id") or "250528"))
            task_id, save_dir = offline.add_into(magnet, self.offline_dir, title)
        except BaiduError as exc:
            if int(getattr(exc, "errno", 0) or 0) == MAGNET_UNSUPPORTED_ERRNO:
                # 百度网盘已不支持磁力离线：这条转人工处理（调度器记进 magnet_pending）
                return SaveResult(False, "magnet", site, error=MAGNET_UNSUPPORTED, detail=magnet[:200])
            return SaveResult(False, "magnet", site, error=str(exc))
        self.log.info("离线任务已建 %s → %s", task_id, save_dir)

        info = offline.wait(task_id, poll_seconds=self.poll_seconds, max_hours=self.offline_wait_hours)
        if info.get("status") != 2:
            offline.delete([task_id])
            return SaveResult(False, "magnet", site,
                              error=f"离线未完成: {info.get('status_text')} (errno={info.get('task_errno')})")

        size_gb = measure(self.client, save_dir)
        if size_gb <= 0:
            size_gb = round(int(info.get("file_size") or 0) / (1000 ** 3), 3)
        if size_gb <= 0:
            return SaveResult(False, "magnet", site, error="离线完成但量不到体积")
        return SaveResult(True, "magnet", site, cloud_path=save_dir, size_gb=size_gb,
                          detail=f"task={task_id}")

    # ---------- B：分享转存 ----------
    def save_share(self, link: str, title: str, site: str = "kungal") -> SaveResult:
        try:
            transfer = ShareTransfer(self.client)
            info = transfer.parse(link)
            # 鲲的正文里常混「模拟器及解压工具」的分享链接，先按真实体积筛一遍再转存
            plain_gb = round(sum(int(f.get("size") or 0) for f in (info.get("files") or [])) / (1000 ** 3), 3)
            if plain_gb and plain_gb < self.min_gb:
                return SaveResult(False, "share", site,
                                  error=f"分享里只有 {plain_gb:.2f}GB，不是游戏本体（可能是模拟器/工具）",
                                  detail=link[:200])
            if plain_gb and plain_gb > self.max_gb:
                return SaveResult(False, "share", site,
                                  error=f"分享体积 {plain_gb:.2f}GB 超出单部上限 {self.max_gb:.0f}GB",
                                  detail=link[:200])
            result = transfer.transfer_into(link, self.share_dir, title, info=info)
        except BaiduError as exc:
            return SaveResult(False, "share", site, error=str(exc))
        cloud_path = str(result.get("cloud_path") or "")
        size_gb = measure(self.client, cloud_path)
        if size_gb <= 0:
            # 列表接口偶尔有延迟：退回用分享页里各文件的 size 求和
            size_gb = round(int(result.get("size_bytes") or 0) / (1000 ** 3), 3)
        if size_gb <= 0:
            return SaveResult(False, "share", site, cloud_path=cloud_path, error="转存成功但量不到体积")
        return SaveResult(True, "share", site, cloud_path=cloud_path, size_gb=size_gb,
                          detail=",".join(result.get("files") or [])[:200])

    # ---------- C：直链 → 加密上传 ----------
    def save_direct(self, url: str, title: str, referer: str = "", site: str = "shinnku") -> SaveResult:
        if not url:
            return SaveResult(False, "direct", site, error="直链为空")
        remaining = self.c_remaining_gb()
        # 先看体积再决定下不下：C 源花的是校园流量，不能下完才发现配额不够
        expected_bytes, _ = self._downloader.probe(url, referer=referer)
        if expected_bytes:
            expected_gb = round(expected_bytes / (1000 ** 3), 3)
            if expected_gb > remaining:
                return SaveResult(False, "direct", site,
                                  error=f"本批 C 源配额只剩 {remaining:.2f}GB，这部要 {expected_gb:.2f}GB")
        try:
            local = self._downloader.download(url, _local_name(url, title), referer=referer)
        except (DownloadError, OSError) as exc:
            return SaveResult(False, "direct", site, error=f"下载失败: {exc}")

        plain_gb = round(local.stat().st_size / (1000 ** 3), 3)
        remaining = self.c_remaining_gb()
        if plain_gb > remaining:
            self._downloader.cleanup(local)
            return SaveResult(False, "direct", site,
                              error=f"本批 C 源配额只剩 {remaining:.2f}GB，这部要 {plain_gb:.2f}GB")

        payload = local
        try:
            if self.encrypt_enabled:
                if not self.password:
                    raise CryptoError("encrypt.password 为空")
                payload = encrypt_file(local, self.password)
        except (CryptoError, OSError) as exc:
            self._downloader.cleanup(local)
            return SaveResult(False, "direct", site, error=f"加密失败: {exc}")

        remote_dir = f"{self.encrypted_dir.rstrip('/')}/{_safe(title)}"
        try:
            uploader = Uploader(self.client, int((self.cfg.get("baidu") or {}).get("upload_chunk_mb") or 4))
            remote_path = uploader.upload(payload, remote_dir)
            if not uploader.verify(remote_path, payload.stat().st_size):
                raise BaiduError(-1, "上传后体积校验不一致", "百度上传")
        except (BaiduError, OSError) as exc:
            self._cleanup(local, payload)
            return SaveResult(False, "direct", site, error=f"上传失败: {exc}")

        self.db.add_c_gb(plain_gb)
        self._cleanup(local, payload)
        size_gb = measure(self.client, remote_path)
        return SaveResult(True, "direct", site, cloud_path=remote_path,
                          size_gb=size_gb or plain_gb, detail=f"下载 {plain_gb:.2f}GB 已加密上传")

    # ---------- 清理 ----------
    def _cleanup(self, *paths: Path) -> None:
        for path in paths:
            try:
                Path(path).unlink(missing_ok=True)
            except OSError as exc:
                self.log.warning("清理本地文件失败 %s: %s", path, exc)

    def cleanup_tmp(self) -> int:
        """清掉上次中断留下的临时文件。"""
        removed = 0
        for path in self._downloader.tmp_dir.glob("*"):
            if path.is_file():
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    pass
        return removed


def _title_for(res: Resource) -> str:
    title = str(res.title or "").strip()
    return title[:80] or "untitled"


def _safe(name: str) -> str:
    import re

    text = re.sub(r'[\\/:*?"<>|\r\n\t]', " ", str(name or "")).strip()
    return (text[:60] or "untitled").strip()


def _local_name(url: str, title: str) -> str:
    """从直链 URL 里取文件名，取不到就用标题 + 后缀。"""
    from urllib.parse import unquote, urlparse

    path = unquote(urlparse(url).path)
    name = path.rsplit("/", 1)[-1] if path else ""
    if "." in name and len(name) < 120:
        return _safe(name)
    return f"{_safe(title)}.zip"
