"""三个渠道都没捞到时的登记与导出（用户要求写进工作区根目录的文档）。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from galsave.db import TaskDB
from galsave.notebook import HEADER, write_missing


class MissingDocCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.db = TaskDB(self.root / "task.db")
        self.addCleanup(self.db.close)
        self.cfg = {"paths": {"missing_doc": str(self.root / "未收录作品.md")}}

    def test_missing_row_is_upserted(self) -> None:
        self.db.add_missing("v562", "サクラノ詩", 8.90, "三站无匹配")
        self.db.add_missing("v562", "サクラノ詩－櫻の森の上を舞う－", 9.10, "分享全失效")
        rows = self.db.missing()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "サクラノ詩－櫻の森の上を舞う－")
        self.assertEqual(self.db.count_missing(), 1)

    def test_mark_done_clears_missing(self) -> None:
        self.db.add_missing("v562", "サクラノ詩", 8.9, "x")
        self.db.mark_done("v562", 8.9, "サクラノ詩", "share", "kungal", "", "/gal_save/v562", 5.0)
        self.assertEqual(self.db.count_missing(), 0)

    def test_forget_missing(self) -> None:
        self.db.add_missing("v1", "A", 7.0, "")
        self.assertTrue(self.db.forget_missing("v1"))
        self.assertFalse(self.db.forget_missing("v1"))

    def test_document_lists_rows(self) -> None:
        self.db.add_missing("v562", "サクラノ詩|副題", 8.90, "三站无匹配")
        path = write_missing(self.cfg, self.db.missing())
        text = path.read_text(encoding="utf-8")
        self.assertIn("三个渠道都没捞到", text)
        self.assertIn("v562", text)
        self.assertIn("サクラノ詩／副題", text)  # 表格里的竖线要转义掉
        self.assertIn("8.90", text)

    def test_document_handles_empty(self) -> None:
        path = write_missing(self.cfg, [])
        text = path.read_text(encoding="utf-8")
        self.assertTrue(text.startswith(HEADER.splitlines()[0]))
        self.assertIn("暂无", text)

    def test_document_is_rewritten_not_appended(self) -> None:
        self.db.add_missing("v1", "A", 7.0, "")
        write_missing(self.cfg, self.db.missing())
        self.db.forget_missing("v1")
        path = write_missing(self.cfg, self.db.missing())
        self.assertNotIn("| v1 |", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
