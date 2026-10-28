"""已收录台账与云端回填命名解析。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from galsave.catalog import parse_folder_name, write_catalog
from galsave.db import TaskDB


class CatalogCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.db = TaskDB(self.root / "task.db")
        self.addCleanup(self.db.close)
        self.cfg = {"paths": {"catalog_doc": str(self.root / "已收录作品.md")}}

    def test_parse_folder(self) -> None:
        self.assertEqual(parse_folder_name("v2002 STEINS;GATE"), ("v2002", "STEINS;GATE"))
        self.assertEqual(parse_folder_name("v2016"), ("v2016", ""))
        self.assertIsNone(parse_folder_name("_share"))
        self.assertIsNone(parse_folder_name("随便一个目录"))

    def test_done_is_dedupe_key(self) -> None:
        self.db.mark_done("v2002", 9.0, "STEINS;GATE", "direct", "shinnku", "", "/gal_save/v2002", 2.6)
        self.assertTrue(self.db.is_done("v2002"))
        self.assertIn("v2002", self.db.done_ids())

    def test_catalog_doc(self) -> None:
        self.db.mark_done("v2002", 9.02, "STEINS;GATE", "direct", "shinnku", "", "/gal_save/v2002", 2.6)
        path = write_catalog(self.cfg, self.db.all_done())
        text = path.read_text(encoding="utf-8")
        self.assertIn("已收录作品", text)
        self.assertIn("v2002", text)
        self.assertIn("STEINS;GATE", text)


if __name__ == "__main__":
    unittest.main()
