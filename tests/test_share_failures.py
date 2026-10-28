# -*- coding: utf-8 -*-
"""转存失败的处理：限流整轮停手、失效分享记 bad_link、只有安卓包的说法。"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from baidu_cloud.client import BaiduError
from galsave.cloud import CloudSaver, SaveResult
from galsave.config import load_config
from galsave.crawler.base import SOURCE_DIRECT, SOURCE_MAGNET, SOURCE_SHARE, Resource
from galsave.db import TaskDB
from galsave.prune import Entry, check_game_like
from galsave.scheduler import Scheduler
from galsave.vndb import Vn


class FakeSaver(CloudSaver):
    def __init__(self, result: SaveResult) -> None:
        self.cfg: dict = {}
        self.db = None
        self.result = result
        self.saved: list[Resource] = []

    def c_remaining_gb(self) -> float:
        return 500.0

    def cleanup_tmp(self) -> int:
        return 0

    def save(self, res, vndb_id="", name=""):  # type: ignore[override]
        self.saved.append(res)
        return self.result


class DbCase(unittest.TestCase):
    """Windows 上临时目录里的 db 文件必须先关连接才能删。"""

    def make_db(self) -> TaskDB:
        tmp = tempfile.mkdtemp(prefix="galsave-test-")
        db = TaskDB(str(Path(tmp) / "t.db"))
        self.addCleanup(shutil.rmtree, tmp, True)
        self.addCleanup(db.close)
        return db


class RateLimitTest(DbCase):
    def test_rate_limit_stops_share_for_this_run(self) -> None:
        db = self.make_db()
        saver = FakeSaver(SaveResult(ok=False, route="share", site="kungal",
                                     error="[baidu 4] 百度网盘转存: 请求过于频繁，被限流了"))
        cfg = load_config()
        cfg["paths"] = {
            "log_dir": str(Path(tempfile.mkdtemp(prefix="galsave-log-")) / "logs"),
            "missing_doc": str(Path(tempfile.mkdtemp(prefix="galsave-doc-")) / "未收录作品.md"),
        }
        sched = Scheduler(cfg, db=db)
        sched.saver = saver
        sched.log.info = lambda *a, **k: None  # 别刷屏
        resource = Resource(site="kungal", source_type=SOURCE_SHARE, title="x",
                            link="https://pan.baidu.com/s/1x", size_gb=2.0)
        sched.engine = mock.Mock()
        sched.engine.find.return_value = [resource]
        sched.skip_types = {SOURCE_DIRECT, SOURCE_MAGNET}
        vn = Vn(id="v1", title="t", alttitle="タイトル", olang="ja", rating=90.0)
        sched._handle(vn)
        self.assertFalse(sched.share_ok)
        self.assertNotIn(SOURCE_SHARE, sched._allow_types())


class DeadShareTest(DbCase):
    def _run(self, errno: int, link: str) -> TaskDB:
        db = self.make_db()
        saver = CloudSaver(load_config(), db, client=mock.Mock())

        class FakeTransfer:
            def __init__(self, client) -> None:  # noqa: ANN001
                pass

            def parse(self, target: str) -> dict:
                raise BaiduError(errno, "boom", "百度网盘转存")

        with mock.patch("galsave.cloud.ShareTransfer", FakeTransfer):
            result = saver.save_share(link, "/gal_save/v1 x", "x")
        self.assertFalse(result.ok)
        return db

    def test_31066_marks_bad_link(self) -> None:
        link = "https://pan.baidu.com/s/1dead"
        db = self._run(31066, link)
        self.assertTrue(db.is_bad_link(link))

    def test_other_errors_are_not_remembered(self) -> None:
        link = "https://pan.baidu.com/s/1busy"
        db = self._run(4, link)
        self.assertFalse(db.is_bad_link(link))


class MobileOnlyTest(unittest.TestCase):
    def test_apk_only_share_is_named_clearly(self) -> None:
        rep = check_game_like([Entry(rel="game.apk", name="game.apk", isdir=False, size=2_646_904_977)])
        self.assertFalse(rep.ok)
        self.assertIn("安卓", rep.reason)
        self.assertNotIn("未知格式", rep.reason)


class PrecheckTest(unittest.TestCase):
    """转存前先按文件清单判一次：视频包不该浪费转存次数（百度会限流）。"""

    def setUp(self) -> None:
        self.saver = CloudSaver.__new__(CloudSaver)

    def test_video_only_share_is_rejected_before_transfer(self) -> None:
        info = {"files": [{"server_filename": "1.mp4", "isdir": 0, "size": 35_000_000_000}]}
        reason = self.saver._precheck_share(info)
        self.assertTrue("视频/文档" in reason or "伪装" in reason, reason)

    def test_real_game_passes(self) -> None:
        info = {"files": [{"server_filename": "game.rar", "isdir": 0, "size": 4_000_000_000}]}
        self.assertEqual(self.saver._precheck_share(info), "")

    def test_folder_without_sizes_is_not_judged(self) -> None:
        info = {"files": [{"server_filename": "10", "isdir": 1, "size": 0}]}
        self.assertEqual(self.saver._precheck_share(info), "")


if __name__ == "__main__":
    unittest.main()
