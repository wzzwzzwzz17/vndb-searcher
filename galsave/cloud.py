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
from baidu_cloud.client import posix_name, posix_parent
from baidu_cloud.offline import MAGNET_UNSUPPORTED, MAGNET_UNSUPPORTED_ERRNO
from baidu_cloud.size import measure

from .config import cget
from .crawler import Resource
from .crawler.base import SOURCE_DIRECT, SOURCE_MAGNET, SOURCE_SHARE
from .crypto.enc import CryptoError, encrypt_file
from .downloader.direct import DirectDownloader, DownloadError
from .downloader.torrent import cleanup_dir
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
        # 全部作品都放在同一个根目录下，一部件一个子目录，名字统一是「v<编号> <日文原名>」
        self.root_dir = str(bcfg.get("root_dir") or "/gal_save")
        self.folder_pattern = str(bcfg.get("folder_pattern") or "v{id} {name}")
        self.magnet_local = bool(bcfg.get("magnet_local", True))
        self.offline_enabled = bool(bcfg.get("offline_enabled", True))
        self.offline_wait_hours = float(bcfg.get("offline_wait_hours") or 6)
        self.poll_seconds = float(bcfg.get("poll_seconds") or 20)
        self.interval = float(bcfg.get("interval") or 1.5)
        self.c_quota_gb = float(cfg.get("c_quota_per_batch_gb") or 500)
        self.min_gb = float(cfg.get("min_resource_gb") or 0.3)
        self.max_gb = float(cfg.get("max_resource_gb") or 60)
        # 入库后体检：不是游戏本体就删掉；多平台包只留 PC
        prune_cfg = cfg.get("prune") or {}
        self.prune_enabled = bool(prune_cfg.get("enabled", True))
        self.prune_threshold_gb = float(prune_cfg.get("threshold_gb") or 9.0)

        enc = cfg.get("encrypt") or {}
        self.encrypt_enabled = bool(enc.get("enabled", True))
        self.password = str(enc.get("password") or "")

        self._client = client
        self._downloader = DirectDownloader(cfg)
        self._aria2: Any = None

    # ---------- 懒加载 ----------
    @property
    def client(self) -> BaiduClient:
        if self._client is None:
            self._client = BaiduClient()
            self._client.require_login()
        return self._client

    @property
    def aria2(self) -> Any:
        if self._aria2 is None:
            from .downloader.torrent import Aria2Downloader

            self._aria2 = Aria2Downloader(self.cfg)
        return self._aria2

    def c_remaining_gb(self) -> float:
        return max(0.0, self.c_quota_gb - float(self.db.c_quota_used_gb()))

    # ---------- 命名 ----------
    def folder_for(self, vndb_id: str, name: str) -> str:
        """`/gal_save/v19073 千恋＊万花`：单层目录，名字带 VNDB 编号。

        `folder_pattern` 里可用 `{id}`（纯数字）、`{vndb_id}` / `{tag}`（带 v 前缀）、`{name}`。
        """
        import re

        raw = str(vndb_id or "").strip().lstrip("vV") or "0"
        label = _safe(name) or f"v{raw}"
        fields = {"id": raw, "vndb_id": f"v{raw}", "tag": f"v{raw}", "name": label}
        try:
            folder = self.folder_pattern.format(**fields)
        except (KeyError, IndexError):
            self.log.warning("folder_pattern %r 里有不认识的占位符，改用默认命名", self.folder_pattern)
            folder = f"v{raw} {label}"
        if not re.match(r"^[vV]\d", folder):
            folder = f"v{raw} {folder}"
        return f"{self.root_dir.rstrip('/')}/{_safe(folder, 90)}"

    def save(self, res: Resource, vndb_id: str = "", name: str = "") -> SaveResult:
        """按资源类型分派到对应路线。`name` 是 VNDB 的日文原名（没有才用英文）。"""
        if res.size_gb and not (self.min_gb <= res.size_gb <= self.max_gb):
            return SaveResult(False, res.source_type, res.site,
                              error=f"体积 {res.size_gb:.2f}GB 不在 {self.min_gb}-{self.max_gb}GB 范围")
        title = (name or "").strip() or _title_for(res)
        folder = self.folder_for(vndb_id, title)
        if res.source_type == SOURCE_MAGNET:
            return self.save_magnet(res.link, folder, title, res.site)
        if res.source_type == SOURCE_SHARE:
            return self.save_share(res.link, folder, title, res.site)
        if res.source_type == SOURCE_DIRECT:
            return self.save_direct(res.link, folder, title, res.detail_url, res.site)
        return SaveResult(False, res.source_type, res.site, error=f"未知来源类型 {res.source_type}")

    # ---------- A：磁力 ----------
    def save_magnet(self, magnet: str, folder: str, title: str = "", site: str = "ggbase") -> SaveResult:
        """磁力：百度已不收离线，优先本机 aria2 下载后再加密上传（占本地流量）。"""
        if not magnet.startswith("magnet:"):
            return SaveResult(False, "magnet", site, error=f"不是磁力链接: {magnet[:40]}")
        if self.magnet_local:
            ok, why = self.aria2.available()
            if ok:
                return self._magnet_via_aria2(magnet, folder, title, site)
            self.log.info("本机没有 aria2（%s），磁力只能登记待处理", why)
        if not self.offline_enabled:
            return SaveResult(False, "magnet", site, error=MAGNET_UNSUPPORTED, detail=magnet[:200])
        try:
            offline = OfflineDownloader(self.client, str((self.cfg.get("baidu") or {}).get("app_id") or "250528"))
            task_id, save_dir = offline.add_into(magnet, folder, title)
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

    def _magnet_via_aria2(self, magnet: str, folder: str, title: str, site: str) -> SaveResult:
        remaining = self.c_remaining_gb()
        if remaining <= 0:
            return SaveResult(False, "magnet", site,
                              error=f"本批 C 源配额只剩 {remaining:.2f}GB，磁力要下到本地才能上传")
        try:
            local = self.aria2.download(magnet, _safe(title) or "magnet")
        except Exception as exc:  # noqa: BLE001 - aria2 报错统一转成失败结果
            return SaveResult(False, "magnet", site, error=f"aria2 下载失败: {exc}")
        plain_gb = round(Path(local).stat().st_size / (1000 ** 3), 3)
        if plain_gb > remaining:
            cleanup_dir(local)
            return SaveResult(False, "magnet", site,
                              error=f"本批 C 源配额只剩 {remaining:.2f}GB，这部要 {plain_gb:.2f}GB")
        try:
            return self._encrypt_and_upload(local, folder, title, site, "magnet", plain_gb)
        finally:
            cleanup_dir(local)

    # ---------- B：分享转存 ----------
    def save_share(self, link: str, folder: str, title: str = "", site: str = "kungal") -> SaveResult:
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
            bait = self._bait_name(info)
            if bait:
                self.db.add_bad_link(link, "标题像课程/资料")
                return SaveResult(False, "share", site,
                                  error=f"分享标题像课程/资料（{bait}），不是游戏本体，跳过",
                                  detail=link[:200])
            demo = self._demo_name(info)
            if demo:
                return SaveResult(False, "share", site,
                                  error=f"分享是试玩版（{demo}），不是完整游戏，跳过",
                                  detail=link[:200])
            # 转存前就按文件清单判一次：凡是明摆着的课程/视频包，不占转存次数
            # （百度连打十几条转存就会限流，把额度留给可能是游戏的分享）
            early = self._precheck_share(info)
            if early:
                if "未知格式" not in early:
                    self.db.add_bad_link(link, early)
                return SaveResult(False, "share", site,
                                  error=f"分享内容不是游戏本体（{early}），跳过",
                                  detail=link[:200])
            result = transfer.transfer(link, folder, info=info)
            result["cloud_path"] = folder
        except BaiduError as exc:
            self._remove_if_empty(folder)
            # 31066 = 分享里没有可转存的文件（实测大量失效/空壳分享都回这个），
            # -9 / 105 = 链接失效。记进 bad_link，下次直接跳过，别每小时重复探测
            if exc.errno in (31066, -9, 105):
                self.db.add_bad_link(link, f"分享已失效（{exc.errno}）")
            return SaveResult(False, "share", site, error=str(exc))
        self._flatten(folder)
        cloud_path = str(result.get("cloud_path") or "")
        verdict = self.inspect_and_prune(cloud_path)
        if verdict is not None and not verdict.ok:
            self.db.add_bad_link(link, verdict.reason)
            return SaveResult(False, "share", site, cloud_path=cloud_path,
                              error=f"分享内容不是游戏本体（{verdict.reason}），已删除并跳过",
                              detail=link[:200])
        size_gb = measure(self.client, cloud_path)
        if size_gb <= 0:
            # 列表接口偶尔有延迟：退回用分享页里各文件的 size 求和
            size_gb = round(int(result.get("size_bytes") or 0) / (1000 ** 3), 3)
        if size_gb <= 0:
            self._remove_if_empty(folder)
            return SaveResult(False, "share", site, cloud_path=cloud_path, error="转存成功但量不到体积")
        return SaveResult(True, "share", site, cloud_path=cloud_path, size_gb=size_gb,
                          detail=",".join(result.get("files") or [])[:200])

    # ---------- C：直链 → 加密上传 ----------
    def save_direct(self, url: str, folder: str, title: str = "", referer: str = "",
                    site: str = "shinnku") -> SaveResult:
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
        try:
            return self._encrypt_and_upload(local, folder, title, site, "direct", plain_gb)
        finally:
            self._downloader.cleanup(local)

    # ---------- 公共尾巴：加密 + 上传 ----------
    def _encrypt_and_upload(self, local: Path, folder: str, title: str, site: str,
                            route: str, plain_gb: float) -> SaveResult:
        local = Path(local)
        payload = local
        try:
            if self.encrypt_enabled:
                if not self.password:
                    raise CryptoError("encrypt.password 为空")
                payload = encrypt_file(local, self.password)
        except (CryptoError, OSError) as exc:
            return SaveResult(False, route, site, error=f"加密失败: {exc}")

        try:
            self.client.ensure_dir(folder)
            uploader = Uploader(self.client, int((self.cfg.get("baidu") or {}).get("upload_chunk_mb") or 4))
            remote_path = uploader.upload(payload, folder)
            if not uploader.verify(remote_path, payload.stat().st_size):
                raise BaiduError(-1, "上传后体积校验不一致", "百度上传")
        except (BaiduError, OSError) as exc:
            if payload != local:
                self._cleanup(payload)
            self._remove_if_empty(folder)
            return SaveResult(False, route, site, error=f"上传失败: {exc}")

        self.db.add_c_gb(plain_gb)
        if payload != local:
            self._cleanup(payload)
        size_gb = measure(self.client, remote_path)
        return SaveResult(True, route, site, cloud_path=remote_path,
                          size_gb=size_gb or plain_gb, detail=f"本地下载 {plain_gb:.2f}GB 已加密上传")

    # ---------- 入库后体检 ----------
    def _bait_name(self, info: dict[str, Any]) -> str:
        """分享根目录名一看就是课程/资料时返回那个名字（转都别转）。"""
        from .prune import looks_like_bait

        for item in info.get("files") or []:
            name = str(item.get("server_filename") or "")
            if looks_like_bait(name):
                return name[:40]
        return ""

    def _demo_name(self, info: dict[str, Any]) -> str:
        """整包都是试玩版时返回那个名字（有完整包混在一起时不动）。"""
        from .prune import looks_like_demo

        names = [str(it.get("server_filename") or "") for it in info.get("files") or []]
        names = [n for n in names if n]
        if names and all(looks_like_demo(n) for n in names):
            return names[0][:40]
        return ""

    def _precheck_share(self, info: dict[str, Any]) -> str:
        """按分享页的文件清单先判一次，返回不能入库的原因（空串表示可以转存）。

        只在「根目录全是文件（没有子目录）」时判断：有子目录时分享页给不出
        子树体积，判了容易误杀（例如分卷包放在 `10/` 里那种）。
        """
        from .prune import check_game_like, make_entries

        files = list(info.get("files") or [])
        if not files or any(int(it.get("isdir") or 0) for it in files):
            return ""
        report = check_game_like(make_entries(files))
        return "" if report.ok else report.reason

    def inspect_and_prune(self, folder: str) -> Any:
        """列一遍入库目录：不是游戏本体就整目录删掉；多平台包只留 PC。

        返回 `prune.Report`（`ok=False` 表示已经删掉），异常时返回 `None`（不影响主流程）。
        """
        if not self.prune_enabled or not folder:
            return None
        try:
            from .prune import apply_report, plan_prune, walk_cloud

            entries = walk_cloud(self.client, folder)
            report = plan_prune(entries, threshold_gb=self.prune_threshold_gb)
            if not report.ok:
                self.log.warning("入库体检：%s（%s）", report.summary(), folder)
                self.client.delete([folder])
                return report
            if report.drop:
                removed = apply_report(self.client, folder, report)
                self.log.info("入库体检：%s → 删掉 %d 项 %.2fGB 的非 PC 内容",
                              report.summary(), removed, report.drop_gb)
            for note in report.warnings:
                self.log.warning("入库体检：%s", note)
            return report
        except BaiduError as exc:
            self.log.warning("入库体检失败（先保留原样）：%s", exc)
            return None
        except Exception as exc:  # noqa: BLE001 - 体检不该打断入库
            self.log.warning("入库体检异常（先保留原样）：%s", exc)
            return None

    # ---------- 清理 ----------
    def _flatten(self, folder: str) -> None:
        """分享自带的根目录会多套一层（`v123 名/v123 名/文件`），把唯一子目录提上来。"""
        parent = posix_parent(folder)
        name = posix_name(folder)
        for _ in range(2):
            try:
                entries = self.client.list_dir(folder)
            except BaiduError:
                return
            if len(entries) != 1 or not entries[0].get("isdir"):
                return
            child = str(entries[0].get("server_filename") or "")
            if not child:
                return
            inner = f"{folder}/{child}"
            aside = f"{folder} __flatten"
            try:
                self.client.move(folder, parent, posix_name(aside))
                self.client.move(inner.replace(folder, aside, 1), parent, name)
                self.client.delete([aside])
                self.log.info("入库目录多套了一层，已提上来：%s", folder)
            except BaiduError as exc:
                self.log.warning("整理嵌套目录失败（先保留原样）：%s", exc)
                return

    def _remove_if_empty(self, folder: str) -> None:
        """失败时别在网盘里留空目录。"""
        try:
            entries = self.client.list_dir(folder)
        except BaiduError:
            return
        if entries:
            return
        try:
            self.client.delete([folder])
            self.log.info("清掉空目录 %s", folder)
        except BaiduError as exc:
            self.log.debug("清空目录失败 %s: %s", folder, exc)

    def _cleanup(self, *paths: Path) -> None:
        for path in paths:
            try:
                Path(path).unlink(missing_ok=True)
            except OSError as exc:
                self.log.warning("清理本地文件失败 %s: %s", path, exc)

    def cleanup_tmp(self) -> int:
        """清掉上次中断留下的临时文件（`.part` / `.aria2` 保留，下次还能续传）。"""
        removed = 0
        for path in self._downloader.tmp_dir.glob("*"):
            if path.is_file() and not path.name.endswith((".part", ".aria2")):
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    pass
        return removed


def _title_for(res: Resource) -> str:
    title = str(res.title or "").strip()
    return title[:80] or "untitled"


def _safe(name: str, limit: int = 60) -> str:
    import re

    text = re.sub(r'[\\/:*?"<>|\r\n\t]', " ", str(name or "")).strip()
    text = " ".join(text.split())
    return (text[:limit] or "untitled").strip()


def _local_name(url: str, title: str) -> str:
    """从直链 URL 里取文件名，取不到就用标题 + 后缀。"""
    from urllib.parse import unquote, urlparse

    path = unquote(urlparse(url).path)
    name = path.rsplit("/", 1)[-1] if path else ""
    if "." in name and len(name) < 120:
        return _safe(name)
    return f"{_safe(title)}.zip"
