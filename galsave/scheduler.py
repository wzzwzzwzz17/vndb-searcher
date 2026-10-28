# -*- coding: utf-8 -*-
"""调度器：分数档 → VNDB 作品 → 三站搜索 → 三源落地 → 入库统计。

主循环逻辑（对应方案文档第二节）：
1. 按 rating_buckets 从高到低走，每档内 VNDB 按 rating 降序；
2. 已经 done 的 vndb_id 直接跳过，永不重复捞；
3. 每部作品按 source_priority 依次试 magnet / share / direct；
4. 任意一条成功就入库，记「网盘实际体积」；
5. 累计体积 >= max_total_gb 且 stop_on_reach_limit 时停；
6. 失败按 source 记重试，下次运行先补重试队列。
"""

from __future__ import annotations

import time
from typing import Any

from baidu_cloud.offline import MAGNET_UNSUPPORTED

from .cloud import CloudSaver, SaveResult
from .config import cget, cpath
from .crawler import SearchEngine
from .crawler.base import SOURCE_DIRECT, SOURCE_MAGNET, SOURCE_SHARE
from .db import TaskDB
from .logger import get_logger, setup_logging
from .vndb import Vn, VndbClient, VndbError


class StopFull(RuntimeError):
    """累计容量到达上限。"""


class Scheduler:
    def __init__(self, cfg: dict[str, Any], db: TaskDB | None = None):
        self.cfg = cfg
        self.log = get_logger()
        self.db = db or TaskDB(cpath(cfg, "paths.db"))
        self.vndb = VndbClient(cfg)
        self.engine = SearchEngine(cfg)
        self.saver = CloudSaver(cfg, self.db)

        self.max_total_gb = float(cfg.get("max_total_gb") or 4000)
        self.stop_on_limit = bool(cfg.get("stop_on_reach_limit", True))
        self.max_retry = int(cfg.get("max_retry") or 3)
        run_cfg = cfg.get("run") or {}
        self.max_vns = int(run_cfg.get("max_vns") or 0)
        self.stop_after_failures = int(run_cfg.get("stop_after_consecutive_failures") or 0)

        self.processed: set[str] = set()
        self.stats = {"success": 0, "failed": 0, "skipped": 0, "vns": 0}
        self.offline_ok = True
        self.magnet_ok = True  # 磁力能不能真交给网盘离线（实测百度已不支持，会变成 False）
        self.c_ok = True
        # 命令行 --skip 指定的来源，整轮都不碰（比如 --skip direct 就完全不占校园流量）
        self.skip_types: set[str] = set()

    # ---------- 上限 ----------
    def total_gb(self) -> float:
        return self.db.total_gb()

    def reached_limit(self) -> bool:
        return self.total_gb() >= self.max_total_gb

    # ---------- 登录 ----------
    def ensure_login(self) -> bool:
        """需要登录时打开真实浏览器窗口，让用户本人登录（不接触密码）。"""
        try:
            from baidu_cloud import BaiduClient
            from baidu_cloud import auth as bd_auth
        except ImportError as exc:  # pragma: no cover
            self.log.error("导入 baidu_cloud 失败: %s", exc)
            return False
        if bd_auth.is_logged_in():
            # 本地有 Cookie 不代表还有效，真去问一次接口
            try:
                if BaiduClient().check_login():
                    return True
                self.log.warning("保存的登录信息已失效（Cookie 过期），需要重新登录")
            except Exception as exc:  # noqa: BLE001 - 网络抖动时不要误判成未登录
                self.log.warning("检查登录态失败（可能只是网络问题）: %s", exc)
                return True
        self.log.warning("还没有可用的百度网盘登录信息，正在打开浏览器窗口，请在其中登录…")
        try:
            return bool(bd_auth.login(force=True))
        except Exception as exc:  # noqa: BLE001 - 浏览器打不开也要给出可操作提示
            self.log.error("打开浏览器登录失败: %s", exc)
            self.log.error("可以手动执行: python -m baidu_cloud.auth login")
            return False

    # ---------- 规划 ----------
    def plan(self, max_pages: int = 10) -> list[dict[str, Any]]:
        """每个分数档各有多少作品（翻页数受 max_pages 限制，仅用于看规模）。"""
        rows: list[dict[str, Any]] = []
        done = self.db.done_ids()
        for bucket in self.cfg.get("rating_buckets") or []:
            count = 0
            candidates = 0
            try:
                page = 1
                while page <= max_pages:
                    items, more = self.vndb.fetch_page(float(bucket), page)
                    count += len(items)
                    candidates += sum(1 for vn in items if vn.id not in done)
                    if not more or not items:
                        break
                    page += 1
            except VndbError as exc:
                self.log.warning("统计 %s 档失败: %s", bucket, exc)
                rows.append({"bucket": float(bucket), "count": -1, "todo": -1, "error": str(exc)})
                continue
            rows.append({"bucket": float(bucket), "count": count, "todo": candidates})
        return rows

    # ---------- 重试 ----------
    def run_retries(self, limit: int = 50) -> int:
        """处理到期的重试。只重试还能找到的作品，已 done 的直接清掉。"""
        handled = 0
        for row in self.db.due_retries()[:limit]:
            vndb_id = str(row["vndb_id"])
            if self.db.is_done(vndb_id):
                self.db.clear_retry(vndb_id, str(row["source_type"]))
                continue
            try:
                vn = self.vndb.get_vn(vndb_id)
            except VndbError as exc:
                self.log.warning("重试取 %s 失败: %s", vndb_id, exc)
                continue
            if not vn:
                self.db.clear_retry(vndb_id, str(row["source_type"]))
                continue
            self.log.info("重试 %s %s", vndb_id, vn.title)
            self.db.clear_retry(vndb_id, str(row["source_type"]))
            if self._handle(vn):
                handled += 1
        return handled

    # ---------- 主流程 ----------
    def run(self, buckets: list[float] | None = None, max_vns: int | None = None,
            skip_retries: bool = False) -> dict[str, Any]:
        setup_logging(cpath(self.cfg, "paths.log_dir"))
        self.db.recompute_total()
        self.log.info("启动：已入库 %d 部 / %.2fGB，上限 %.0fGB",
                      self.db.count_done(), self.total_gb(), self.max_total_gb)

        if not self.ensure_login():
            raise SystemExit("没有百度网盘登录态，无法入库。先执行: python -m baidu_cloud.auth login")
        if self.saver.cleanup_tmp():
            self.log.info("清理了上次残留的临时文件")

        if not skip_retries:
            if self.run_retries():
                self.log.info("重试成功 %d 部", self.stats["success"])

        targets = [float(b) for b in (buckets or self.cfg.get("rating_buckets") or [])]
        limit = int(max_vns or self.max_vns or 0)
        consecutive_failures = 0
        started = time.time()

        try:
            for bucket in targets:
                if self.reached_limit():
                    raise StopFull
                self.log.info("—— 分数档 %.1f 开始（已有 %.2fGB）——", bucket, self.total_gb())
                page = 1
                while True:
                    items, more = self.vndb.fetch_page(bucket, page)
                    for vn in items:
                        if vn.id in self.processed or self.db.is_done(vn.id):
                            self.stats["skipped"] += 1
                            continue
                        self.processed.add(vn.id)
                        self.stats["vns"] += 1
                        if self.reached_limit():
                            raise StopFull
                        ok = self._handle(vn)
                        consecutive_failures = 0 if ok else consecutive_failures + 1
                        if limit and self.stats["vns"] >= limit:
                            self.log.info("达到本次运行上限 %d 部", limit)
                            return self._summary(started)
                        if self.stop_after_failures and consecutive_failures >= self.stop_after_failures:
                            self.log.error("连续失败 %d 部，自动停止（站点可能改版）", consecutive_failures)
                            return self._summary(started)
                    if not more or not items:
                        break
                    page += 1
                    if limit and self.stats["vns"] >= limit:
                        return self._summary(started)
        except StopFull:
            if self.stop_on_limit:
                self.log.info("累计 %.2fGB 已达上限 %.0fGB，停止发起新任务",
                              self.total_gb(), self.max_total_gb)
            else:
                self.log.info("已达上限但配置为继续，跳过剩余作品")
        except KeyboardInterrupt:
            self.log.warning("收到中断，保存进度后退出")
        return self._summary(started)

    def _summary(self, started: float) -> dict[str, Any]:
        elapsed = time.time() - started
        summary = {
            **self.stats,
            "total_gb": self.total_gb(),
            "entries": self.db.count_done(),
            "elapsed_min": round(elapsed / 60, 1),
        }
        self.log.info(
            "结束：本次成功 %d，跳过 %d，失败 %d，共 %.2fGB / %d 部，用时 %.1f 分钟",
            summary["success"], summary["skipped"], summary["failed"],
            summary["total_gb"], summary["entries"], summary["elapsed_min"],
        )
        return summary

    # ---------- 单部作品 ----------
    def _handle(self, vn: Vn) -> bool:
        names = vn.names
        self.db.mark_seen(vn.id, vn.display_rating, vn.title)
        self.log.info("▶ %s 《%s》 评分 %s", vn.id, vn.preferred_name, vn.display_rating)

        allow = self._allow_types()
        if not allow:
            self.log.warning("离线不可用且 C 源配额已用完，只能等下一批")
            self.db.add_retry(vn.id, "share", "all", "没有可用来源", self.max_retry)
            self.stats["failed"] += 1
            return False

        try:
            resources = self.engine.find(names, allow_types=allow)
        except Exception as exc:  # noqa: BLE001 - 搜索层不该崩主循环
            self.log.warning("搜索异常: %s", exc)
            self.db.add_retry(vn.id, "search", "all", str(exc), self.max_retry)
            self.stats["failed"] += 1
            return False

        if not resources:
            self.log.info("  三站都没找到匹配资源")
            self.db.add_retry(vn.id, "search", "all", "三站无匹配", self.max_retry)
            self.stats["failed"] += 1
            return False

        for res in resources:
            if self.reached_limit():
                return False
            try:
                if not res.link and res.source_type in (SOURCE_MAGNET, SOURCE_DIRECT, SOURCE_SHARE):
                    self.engine.resolve(res)
            except Exception as exc:  # noqa: BLE001 - 单个资源解析失败继续下一条
                self.log.warning("  解析 %s 链接失败: %s", res.site, exc)
                self.db.add_retry(vn.id, res.source_type, res.site, str(exc), self.max_retry)
                continue

            if res.link and self.db.is_bad_link(res.link):
                self.log.info("  跳过已知是诱饵/空壳的分享（%s）", res.site)
                self.stats["skipped"] += 1
                continue

            projected = self.total_gb() + float(res.size_gb or 0)
            if res.size_gb and projected > self.max_total_gb:
                self.log.info("  跳过 %.2fGB 的 %s（会超过总上限）", res.size_gb, res.site)
                continue

            self.log.info("  尝试 %s", res)
            if res.source_type == SOURCE_MAGNET and not self.magnet_ok:
                # 已知网盘不收磁力离线：不再白发请求，直接登记待处理
                self._record_pending_magnet(vn, res)
                continue
            result = self.saver.save(res, vndb_id=vn.id, name=vn.preferred_name)
            if result.ok:
                self.db.mark_done(
                    vndb_id=vn.id,
                    rating=vn.display_rating,
                    title=vn.preferred_name,
                    source_type=result.route,
                    site=result.site or res.site,
                    resource_link=(res.link or "")[:500],
                    cloud_path=result.cloud_path,
                    size_gb=result.size_gb,
                )
                self.stats["success"] += 1
                self.log.info("  ✓ 入库 %s（%.2fGB，累计 %.2fGB）",
                              result.cloud_path, result.size_gb, self.total_gb())
                return True

            self.log.warning("  ✗ %s", result.error)
            if MAGNET_UNSUPPORTED in str(result.error):
                # 百度网盘不再接受磁力离线：之后每条磁力都只登记、不再调接口
                self.magnet_ok = False
                self._record_pending_magnet(vn, res, result.error)
                continue
            if "SVIP" in str(result.error):
                self.offline_ok = False
                self.log.warning("账号不支持离线下载，后续只用 share/direct")
            if "配额" in str(result.error):
                self.c_ok = False
            self.db.add_retry(vn.id, result.route, res.site, result.error, self.max_retry)

        self.stats["failed"] += 1
        return False

    def _record_pending_magnet(self, vn: Vn, res: Any, note: str = "") -> None:
        self.db.add_pending_magnet(
            vndb_id=vn.id,
            title=vn.preferred_name,
            magnet=res.link or "",
            size_gb=float(res.size_gb or 0),
            site=res.site,
            note=note or MAGNET_UNSUPPORTED,
        )
        self.log.info("  磁力已记入待处理清单（python -m galsave magnets 可导出）")

    def _allow_types(self) -> list[str]:
        """按当前可用性收缩来源列表。

        磁力永远保留：即便网盘不收磁力离线（实测 36001），也仍然把磁力登记到
        magnet_pending，方便之后用本地 BT 客户端或其它方式处理。
        `skip_types` 是命令行强制跳过的来源（比如只想用转存、完全不碰校园流量）。
        """
        priority = list(self.cfg.get("source_priority") or ["share", "direct", "magnet"])
        allow = []
        for source in priority:
            if source in self.skip_types:
                continue
            if source == SOURCE_DIRECT and (not self.c_ok or self.saver.c_remaining_gb() <= 0):
                if self.saver.c_remaining_gb() <= 0:
                    self.log.warning("C 源本批配额已用完，暂时不下载直链。换批可执行: python -m galsave quota --reset")
                continue
            allow.append(source)
        return allow
