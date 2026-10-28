# -*- coding: utf-8 -*-
"""调度器的离线测试：用假爬虫和假上传器验证「不入库不该记 done」这类规则。

真实网络路径由 `python -m galsave dryrun` 和手工跑 `run` 覆盖，这里只测决策逻辑。
"""

from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path
from typing import Any

from baidu_cloud.offline import MAGNET_UNSUPPORTED
from galsave.cloud import CloudSaver, SaveResult
from galsave.config import load_config
from galsave.crawler.base import SOURCE_DIRECT, SOURCE_MAGNET, SOURCE_SHARE, Resource
from galsave.db import TaskDB
from galsave.scheduler import Scheduler, StopFull
from galsave.vndb import Vn


def make_vn(vid: str = "v20802", rating: float = 90.6) -> Vn:
    return Vn(
        id=vid,
        title="Rance X -Kessen-",
        alttitle="ランス10－決戦－",
        olang="ja",
        rating=rating,
        aliases=["R10"],
    )


def make_resource(size_gb: float = 3.77, source: str = SOURCE_MAGNET,
                  link: str = "magnet:?xt=urn:btih:0123456789abcdef") -> Resource:
    return Resource(
        site="ggbase",
        source_type=source,
        title="[ALICESOFT] ランス10 -決戦-",
        link=link,
        size_gb=size_gb,
        detail_url="https://ggb.dlgal.com/view.so?id=86784",
        match_score=90.0,
    )


class FakeSaver(CloudSaver):
    """不碰网络：直接返回预设结果。"""

    def __init__(self, results: list[SaveResult], quota_gb: float = 500.0):
        self.cfg: dict[str, Any] = {}
        self.db = None
        self._results = list(results)
        self._quota = quota_gb
        self.saved: list[Resource] = []

    def c_remaining_gb(self) -> float:
        return self._quota

    def cleanup_tmp(self) -> int:
        return 0

    def save(self, res: Resource, vndb_id: str = "", name: str = "") -> SaveResult:  # type: ignore[override]
        self.saved.append(res)
        if self._results:
            return self._results.pop(0)
        return SaveResult(ok=False, route=res.source_type, site=res.site, error="没有更多预设结果")


class SchedulerDecisionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        root = Path(self.tmpdir.name)
        self.cfg = load_config()
        self.cfg["paths"] = {
            "db": str(root / "task.db"),
            "log_dir": str(root / "logs"),
            "missing_doc": str(root / "未收录作品.md"),
        }
        self.cfg["rating_buckets"] = [8.0]
        self.db = TaskDB(self.cfg["paths"]["db"])
        self.sched = Scheduler(self.cfg, db=self.db)

    def tearDown(self) -> None:
        self.db.close()
        # setup_logging 会打开轮转日志文件，Windows 上不关掉就删不掉临时目录
        logger = logging.getLogger("galsave")
        for handler in logger.handlers[:]:
            handler.close()
            logger.removeHandler(handler)
        self.tmpdir.cleanup()

    def _install(self, vns: list[Vn], resources: list[Resource], saver: FakeSaver) -> None:
        self.sched.vndb.fetch_page = lambda bucket, page=1, results=None: (vns if page == 1 else [], False)
        self.sched.engine.find = lambda names, allow_types=None: list(resources)
        self.sched.engine.resolve = lambda res: res  # 不真的去解析磁力
        self.sched.saver = saver
        self.sched.ensure_login = lambda: True  # 不弹浏览器
        self.sched.run_retries = lambda limit=50: 0

    def test_success_marks_done(self) -> None:
        saver = FakeSaver([SaveResult(ok=True, route=SOURCE_MAGNET, site="ggbase",
                                      cloud_path="/gal_save/v20802 ランス10－決戦－", size_gb=3.77)])
        self._install([make_vn()], [make_resource()], saver)
        summary = self.sched.run(skip_retries=True)
        self.assertEqual(summary["success"], 1)
        self.assertEqual(summary["failed"], 0)
        self.assertTrue(self.db.is_done("v20802"))
        self.assertAlmostEqual(self.db.total_gb(), 3.77, places=2)
        row = self.db.get_done("v20802")
        self.assertEqual(row["source_type"], SOURCE_MAGNET)
        # 入库记录用 VNDB 的日文原名（用户要求按日文名命名）
        self.assertEqual(row["title"], "ランス10－決戦－")

    def test_upload_failure_stops_trying_more_direct_candidates(self) -> None:
        # 上传通道挂了就别再下第二条直链，白花校园流量
        first = make_resource(source=SOURCE_DIRECT, link="https://zd.shinnku.top/file/a.rar")
        second = make_resource(source=SOURCE_DIRECT, link="https://zd.shinnku.top/file/b.rar")
        saver = FakeSaver([SaveResult(ok=False, route=SOURCE_DIRECT, site="shinnku",
                                      error="上传失败: [baidu -1] 连接被重置")])
        self._install([make_vn()], [first, second], saver)
        summary = self.sched.run(skip_retries=True)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(len(saver.saved), 1)  # 第二条没被尝试
        self.assertFalse(self.sched.c_ok)

    def test_bad_link_is_skipped(self) -> None:
        # 已知是诱饵的分享不该再花一次转存
        self.db.add_bad_link("https://pan.baidu.com/s/1bad?pwd=x", "课程/资料")
        bad = make_resource(size_gb=3.0, source=SOURCE_SHARE,
                            link="https://pan.baidu.com/s/1bad?pwd=x")
        good = make_resource(size_gb=2.0, source=SOURCE_SHARE,
                             link="https://pan.baidu.com/s/1good?pwd=y")
        saver = FakeSaver([SaveResult(ok=True, route=SOURCE_SHARE, site="kungal",
                                      cloud_path="/gal_save/v1 x", size_gb=2.0)])
        self._install([make_vn()], [bad, good], saver)
        summary = self.sched.run(skip_retries=True)
        self.assertEqual([r.link for r in saver.saved], ["https://pan.baidu.com/s/1good?pwd=y"])
        self.assertEqual(summary["skipped"], 1)
        self.assertEqual(summary["success"], 1)

    def test_already_done_is_skipped(self) -> None:
        self.db.mark_done("v20802", 9.06, "Rance X -Kessen-", SOURCE_MAGNET, "ggbase", "magnet:?x", "/p", 3.77)
        saver = FakeSaver([])
        self._install([make_vn()], [make_resource()], saver)
        summary = self.sched.run(skip_retries=True)
        self.assertEqual(summary["success"], 0)
        self.assertEqual(summary["skipped"], 1)
        self.assertEqual(saver.saved, [])

    def test_failed_save_records_retry_not_done(self) -> None:
        saver = FakeSaver([SaveResult(ok=False, route=SOURCE_MAGNET, site="ggbase", error="该操作需要百度网盘 SVIP")])
        self._install([make_vn()], [make_resource()], saver)
        self.sched.run(skip_retries=True)
        self.assertFalse(self.db.is_done("v20802"))
        self.assertGreaterEqual(self.db.retry_count(), 1)
        self.assertFalse(self.sched.offline_ok)  # 识别出账号不支持离线
        # 磁力仍然保留在来源列表里（只登记，不再真的请求接口）
        self.assertIn(SOURCE_MAGNET, self.sched._allow_types())

    def test_magnet_unsupported_is_recorded_for_manual_use(self) -> None:
        saver = FakeSaver([SaveResult(ok=False, route=SOURCE_MAGNET, site="ggbase",
                                      error=MAGNET_UNSUPPORTED)])
        self._install([make_vn()], [make_resource()], saver)
        self.sched.run(skip_retries=True)
        self.assertFalse(self.sched.magnet_ok)
        self.assertFalse(self.db.is_done("v20802"))
        pending = self.db.pending_magnets()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["vndb_id"], "v20802")
        self.assertEqual(pending[0]["magnet"], "magnet:?xt=urn:btih:0123456789abcdef")
        # 记入待处理就不算「重试」：不会被反复重跑
        self.assertEqual(self.db.retry_count(), 0)

    def test_magnet_skips_api_once_known_unsupported(self) -> None:
        self.sched.magnet_ok = False
        saver = FakeSaver([SaveResult(ok=True, route=SOURCE_MAGNET, site="ggbase", cloud_path="/p", size_gb=1.0)])
        self._install([make_vn()], [make_resource()], saver)
        self.sched.run(skip_retries=True)
        self.assertEqual(saver.saved, [])  # 没再去调离线接口
        self.assertEqual(len(self.db.pending_magnets()), 1)

    def test_magnet_tries_are_capped_per_vn(self) -> None:
        """老磁力基本没种：一部只真试 max_magnet_tries 条，其余只登记。"""
        resources = [
            make_resource(link="magnet:?xt=urn:btih:aaaa"),
            make_resource(link="magnet:?xt=urn:btih:bbbb"),
            make_resource(link="magnet:?xt=urn:btih:cccc"),
        ]
        saver = FakeSaver([SaveResult(ok=False, route=SOURCE_MAGNET, site="ggbase", error="离线任务失败")])
        self._install([make_vn()], resources, saver)
        self.sched.max_magnet_tries = 1
        self.sched.run(skip_retries=True)
        self.assertEqual(len(saver.saved), 1)  # 只真下了第一条
        # magnet_pending 以 vndb_id 为主键，一部作品只留最后登记的那条磁力
        pending = self.db.pending_magnets()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["magnet"], "magnet:?xt=urn:btih:cccc")

    def test_resource_over_limit_is_skipped(self) -> None:
        saver = FakeSaver([SaveResult(ok=True, route=SOURCE_MAGNET, site="ggbase", cloud_path="/p", size_gb=1.0)])
        self.sched.max_total_gb = 1.0
        self._install([make_vn()], [make_resource(size_gb=3.77)], saver)
        self.sched.run(skip_retries=True)
        self.assertEqual(saver.saved, [])  # 单条就超上限，不该去下载
        self.assertFalse(self.db.is_done("v20802"))

    def test_reach_limit_stops_before_next_vn(self) -> None:
        self.db.mark_done("v1", 9.0, "x", SOURCE_MAGNET, "ggbase", "m", "/p", 4000.0)
        saver = FakeSaver([])
        self._install([make_vn()], [make_resource()], saver)
        self.assertTrue(self.sched.reached_limit())
        summary = self.sched.run(skip_retries=True)
        self.assertEqual(summary["success"], 0)
        self.assertEqual(saver.saved, [])

    def test_allow_types_shrinks_when_direct_unavailable(self) -> None:
        self.sched.offline_ok = False
        self.sched.c_ok = False
        # 磁力始终保留（只登记待处理），直链靠本地配额，配额没断时也能用
        self.assertEqual(self.sched._allow_types(), [SOURCE_SHARE, SOURCE_MAGNET])
        self.sched.c_ok = True
        self.assertEqual(self.sched._allow_types(), [SOURCE_SHARE, SOURCE_DIRECT, SOURCE_MAGNET])

    def test_allow_types_skips_direct_when_quota_used(self) -> None:
        self.sched.saver = FakeSaver([], quota_gb=0.0)
        self.assertNotIn(SOURCE_DIRECT, self.sched._allow_types())

    def test_stop_full_is_raised_by_limit_check(self) -> None:
        # StopFull 是 run 内部的控制流信号，这里确认上限判定本身可靠
        self.db.mark_done("v2", 9.0, "y", SOURCE_MAGNET, "ggbase", "m", "/p", 50.0)
        self.sched.max_total_gb = 10.0
        with self.assertRaises(StopFull):
            if self.sched.reached_limit():
                raise StopFull


    def test_skip_types_removes_requested_sources(self) -> None:
        self.sched.skip_types = {SOURCE_DIRECT}
        self.assertEqual(self.sched._allow_types(), [SOURCE_SHARE, SOURCE_MAGNET])
        self.sched.skip_types = {SOURCE_SHARE, SOURCE_DIRECT, SOURCE_MAGNET}
        self.assertEqual(self.sched._allow_types(), [])


if __name__ == "__main__":
    unittest.main()
