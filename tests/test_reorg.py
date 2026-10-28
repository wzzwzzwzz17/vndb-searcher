# -*- coding: utf-8 -*-
"""云端目录整理（reorg）的离线测试：用假客户端，不动真实网盘。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from galsave.cloud import CloudSaver
from galsave.config import load_config
from galsave.db import TaskDB
from galsave.reorg import MOVE_DIR, MOVE_FILE, SKIP, apply_reorg, plan_reorg


class FakeClient:
    """只有 entry/move/ensure_dir 的假网盘。"""

    def __init__(self, entries: dict[str, bool]):
        # 路径 -> 是否目录
        self.entries = dict(entries)
        self.moves: list[tuple[str, str, str]] = []
        self.created: list[str] = []

    def entry(self, remote_path: str) -> dict | None:
        if remote_path in self.entries:
            return {"server_filename": remote_path.rsplit("/", 1)[-1],
                    "isdir": 1 if self.entries[remote_path] else 0}
        return None

    def ensure_dir(self, remote_dir: str) -> str:
        self.created.append(remote_dir)
        return remote_dir

    def move(self, remote_path: str, dest_dir: str, new_name: str = "") -> bool:
        name = new_name or remote_path.rsplit("/", 1)[-1]
        self.moves.append((remote_path, dest_dir, name))
        del self.entries[remote_path]
        self.entries[f"{dest_dir}/{name}"] = False
        return True


class ReorgTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.cfg = load_config()
        self.cfg["baidu"]["root_dir"] = "/gal_save"
        self.db = TaskDB(self.root / "t.db")
        self.saver = CloudSaver(self.cfg, self.db)

    def tearDown(self) -> None:
        self.db.close()
        self.tmpdir.cleanup()

    def _row(self, vndb_id: str, title: str, cloud_path: str, size_gb: float = 3.0) -> None:
        self.db.mark_done(vndb_id, 8.5, title, "share", "kungal", "link", cloud_path, size_gb)

    def test_compliant_path_is_skipped(self) -> None:
        self._row("v19073", "千恋＊万花", "/gal_save/v19073 千恋＊万花")
        items = plan_reorg(self.saver, self.db, FakeClient({}))
        self.assertEqual(items[0].kind, SKIP)
        self.assertIn("已经合规", items[0].note)

    def test_old_share_dir_is_moved(self) -> None:
        self._row("v19073", "千恋＊万花", "/gal_save/_share/千恋＊万花")
        client = FakeClient({"/gal_save/_share/千恋＊万花": True})
        items = plan_reorg(self.saver, self.db, client)
        self.assertEqual(items[0].kind, MOVE_DIR)
        self.assertEqual(items[0].target, "/gal_save/v19073 千恋＊万花")
        self.assertEqual(apply_reorg(self.saver, self.db, client, items), 1)
        self.assertEqual(client.moves, [("/gal_save/_share/千恋＊万花", "/gal_save",
                                         "v19073 千恋＊万花")])
        row = self.db.get_done("v19073")
        self.assertEqual(row["cloud_path"], "/gal_save/v19073 千恋＊万花")
        self.assertAlmostEqual(row["size_gb"], 3.0, places=3)
        self.assertEqual(row["source_type"], "share")

    def test_file_inside_wrong_dir_is_moved_into_folder(self) -> None:
        self._row("v20802", "ランス10－決戦－", "/gal_save/_encrypted/TEST/作品.png", 4.17)
        client = FakeClient({"/gal_save/_encrypted/TEST/作品.png": False})
        items = plan_reorg(self.saver, self.db, client)
        self.assertEqual(items[0].kind, MOVE_FILE)
        self.assertEqual(items[0].target, "/gal_save/v20802 ランス10－決戦－/作品.png")
        self.assertEqual(apply_reorg(self.saver, self.db, client, items), 1)
        self.assertEqual(client.created, ["/gal_save/v20802 ランス10－決戦－"])
        self.assertEqual(self.db.get_done("v20802")["cloud_path"],
                         "/gal_save/v20802 ランス10－決戦－/作品.png")

    def test_missing_cloud_path_is_skipped(self) -> None:
        self._row("v1", "x", "/gal_save/_share/gone")
        client = FakeClient({})
        items = plan_reorg(self.saver, self.db, client)
        self.assertEqual(items[0].kind, SKIP)
        self.assertIn("云端已不存在", items[0].note)
        self.assertEqual(apply_reorg(self.saver, self.db, client, items), 0)

    def test_empty_cloud_path_is_skipped(self) -> None:
        self._row("v2", "y", "")
        items = plan_reorg(self.saver, self.db, FakeClient({}))
        self.assertEqual(items[0].kind, SKIP)
        self.assertIn("没有云端路径", items[0].note)


if __name__ == "__main__":
    unittest.main()
