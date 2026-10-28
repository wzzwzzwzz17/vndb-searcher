"""转存成功后的解压码登记与文档导出。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from galsave.crawler.kungal import _find_archive_password
from galsave.db import TaskDB
from galsave.passwords import HEADER, write_passwords
from galsave.crawler.base import SOURCE_SHARE, Resource
from galsave.scheduler import Scheduler


class ArchivePasswordParseCase(unittest.TestCase):
    def test_find_from_chinese_label(self) -> None:
        self.assertEqual(_find_archive_password("解压码：sakura2024"), "sakura2024")
        self.assertEqual(_find_archive_password("解压密码: `mugen` 请自取"), "mugen")

    def test_empty_when_absent(self) -> None:
        self.assertEqual(_find_archive_password("只有百度链接 https://pan.baidu.com/s/1abc?pwd=t7cp"), "")


class PasswordDocCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.db = TaskDB(self.root / "task.db")
        self.addCleanup(self.db.close)
        self.cfg = {"paths": {"password_doc": str(self.root / "解压码.md")}}

    def test_set_and_export(self) -> None:
        self.db.set_password("v562", "サクラノ詩", "sakura", "abcd", site="kungal")
        path = write_passwords(self.cfg, self.db.passwords())
        text = path.read_text(encoding="utf-8")
        self.assertTrue(text.startswith(HEADER.splitlines()[0]))
        self.assertIn("v562", text)
        self.assertIn("sakura", text)
        self.assertIn("abcd", text)

    def test_empty_doc(self) -> None:
        path = write_passwords(self.cfg, [])
        self.assertIn("暂无", path.read_text(encoding="utf-8"))

    def test_scheduler_records_on_share_success(self) -> None:
        cfg = {
            "rating_buckets": [8.0],
            "max_total_gb": 4000,
            "source_priority": ["share"],
            "max_retry": 3,
            "c_quota_per_batch_gb": 500,
            "paths": {
                "db": str(self.root / "task.db"),
                "log_dir": str(self.root / "logs"),
                "password_doc": str(self.root / "解压码.md"),
                "missing_doc": str(self.root / "未收录.md"),
            },
        }
        sched = Scheduler(cfg, db=self.db)
        res = Resource(
            site="kungal",
            source_type=SOURCE_SHARE,
            title="千恋＊万花",
            link="https://pan.baidu.com/s/1abc?pwd=t7cp",
            detail_url="https://www.kungal.com/galgame/resource/1",
            extra={"archive_password": "yuzu", "pwd": "t7cp"},
        )
        # Use a minimal fake Vn-like: call _record_password directly
        fake_vn = MagicMock()
        fake_vn.id = "v19073"
        fake_vn.preferred_name = "千恋＊万花"
        sched._record_password(fake_vn, res)
        row = self.db.get_password("v19073")
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["archive_password"], "yuzu")
        self.assertEqual(row["share_pwd"], "t7cp")
        doc = (self.root / "解压码.md").read_text(encoding="utf-8")
        self.assertIn("千恋＊万花", doc)
        self.assertIn("yuzu", doc)


if __name__ == "__main__":
    unittest.main()
