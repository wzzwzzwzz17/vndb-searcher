# -*- coding: utf-8 -*-
"""CloudSaver 三条路线的离线测试：用假上传/假离线/假转存，不联网。

重点验证方案文档里写死的几条规矩：
- 入库体积要用网盘实际大小，量不到就别记；
- C 路线（直链）下载前先看本批 500GB 配额；
- 加密后的文件才是上传的对象，本地明文最后要清掉；
- 体积超出 min/max 范围的资源不接。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from galsave import cloud as cloud_mod
from galsave.cloud import CloudSaver
from galsave.config import load_config
from galsave.crawler.base import SOURCE_DIRECT, SOURCE_MAGNET, SOURCE_SHARE, Resource
from galsave.db import TaskDB


class FakeDownloader:
    """假下载器：直接写一个文件，记录调用。"""

    def __init__(self, root: Path, size_bytes: int = 8 * 1024 * 1024, probe_bytes: int | None = 8 * 1024 * 1024):
        self.root = root
        self.tmp_dir = root / "tmp"
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        self.size_bytes = size_bytes
        self.probe_bytes = probe_bytes
        self.downloaded: list[str] = []

    def probe(self, url: str, referer: str = "") -> tuple[int, bool]:
        return (self.probe_bytes or 0), True

    def download(self, url: str, filename: str, referer: str = "", progress=None, retries: int = 3) -> Path:
        self.downloaded.append(filename)
        target = self.tmp_dir / filename
        target.write_bytes(b"\0" * self.size_bytes)
        return target

    def cleanup(self, path) -> None:
        Path(path).unlink(missing_ok=True)


class FakeOffline:
    instances: list["FakeOffline"] = []
    status = 2
    file_size = 4 * 1024 ** 3

    def __init__(self, client, app_id="250528"):
        self.tasks: list[dict] = []
        FakeOffline.instances.append(self)

    def add_into(self, magnet: str, root_dir: str, title: str):
        return "task-1", f"{root_dir}/{title}"

    def wait(self, task_id: str, poll_seconds: float = 20, max_hours: float = 6, on_tick=None) -> dict:
        return {"status": FakeOffline.status, "status_text": "已完成", "task_errno": 0,
                "file_size": FakeOffline.file_size}

    def delete(self, task_ids) -> bool:
        return True


class FakeTransfer:
    def __init__(self, client):
        self.client = client

    def transfer_into(self, link: str, root_dir: str, title: str) -> dict:
        return {"cloud_path": f"{root_dir}/{title}", "files": ["a.rar"]}


class FakeUploader:
    uploaded: list[tuple[str, str]] = []

    def __init__(self, client, chunk_mb: int = 4):
        self.chunk_mb = chunk_mb

    def upload(self, local_path, remote_dir: str, progress=None) -> str:
        FakeUploader.uploaded.append((str(local_path), f"{remote_dir}/x.png"))
        return f"{remote_dir}/x.png"

    def verify(self, remote_path: str, expect_bytes: int, tolerance: float = 0.01) -> bool:
        return True


class FakeClient:
    """假网盘客户端：只提供 baidu_cloud 各模块会碰到的属性。"""

    app_id = "250528"

    def require_login(self) -> "FakeClient":
        return self


class CloudSaverRouteTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.cfg = load_config()
        self.cfg["encrypt"] = {"enabled": True, "password": "test-pass"}
        self.db = TaskDB(self.root / "task.db")
        self.saver = CloudSaver(self.cfg, self.db)
        # 直接塞假客户端：这些用例不该碰本机 credentials.json，也不该联网
        self.saver._client = FakeClient()
        self.saver._downloader = FakeDownloader(self.root)
        FakeOffline.instances.clear()
        FakeUploader.uploaded.clear()

        patches = [
            mock.patch.object(cloud_mod, "OfflineDownloader", FakeOffline),
            mock.patch.object(cloud_mod, "ShareTransfer", FakeTransfer),
            mock.patch.object(cloud_mod, "Uploader", FakeUploader),
            mock.patch.object(cloud_mod, "measure", lambda client, path, recursive=True: 4.0),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        self.db.close()
        self.tmpdir.cleanup()

    def test_magnet_uses_cloud_size(self) -> None:
        result = self.saver.save_magnet("magnet:?xt=urn:btih:abc", "ランス10", "ggbase")
        self.assertTrue(result.ok)
        self.assertEqual(result.route, SOURCE_MAGNET)
        self.assertGreater(result.size_gb, 0)
        self.assertEqual(self.db.c_quota_used_gb(), 0.0)  # 磁力不占本地流量，不记 C 配额

    def test_magnet_incomplete_task_is_failure(self) -> None:
        FakeOffline.status = 3
        try:
            result = self.saver.save_magnet("magnet:?xt=urn:btih:abc", "ランス10")
        finally:
            FakeOffline.status = 2
        self.assertFalse(result.ok)
        self.assertIn("离线未完成", result.error)

    def test_share_transfer(self) -> None:
        result = self.saver.save_share("https://pan.baidu.com/s/1abc?pwd=t7cp", "ランス10", "kungal")
        self.assertTrue(result.ok)
        self.assertEqual(result.route, SOURCE_SHARE)
        self.assertIn("_share", result.cloud_path)

    def test_direct_downloads_encrypts_and_uploads(self) -> None:
        result = self.saver.save_direct("https://zd.shinnku.top/file/a.rar", "戦国ランス")
        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.route, SOURCE_DIRECT)
        # 上传的应该是加密后的 .png，不是原始 .rar
        self.assertEqual(len(FakeUploader.uploaded), 1)
        uploaded_local = Path(FakeUploader.uploaded[0][0])
        self.assertEqual(uploaded_local.suffix, ".png")
        self.assertFalse(uploaded_local.exists())  # 传完本地要清掉
        self.assertGreater(self.db.c_quota_used_gb(), 0)  # 直链占本地流量，要记配额

    def test_direct_blocked_by_remaining_quota(self) -> None:
        # 这一批只剩 0.01GB，文件 40MB 也放不下
        self.db.add_c_gb(499.99)
        self.saver._downloader.size_bytes = 40 * 1024 * 1024
        result = self.saver.save_direct("https://zd.shinnku.top/file/a.rar", "大きい作品")
        self.assertFalse(result.ok)
        self.assertIn("配额", result.error)
        self.assertEqual(FakeUploader.uploaded, [])

    def test_direct_checks_quota_before_downloading(self) -> None:
        # 校园流量很贵：体积能从头拿到时，配额不够就根本不该开下
        self.db.add_c_gb(499.99)
        self.saver._downloader.probe_bytes = 200 * 1024 * 1024
        result = self.saver.save_direct("https://zd.shinnku.top/file/big.rar", "でかい")
        self.assertFalse(result.ok)
        self.assertIn("配额", result.error)
        self.assertEqual(self.saver._downloader.downloaded, [])

    def test_direct_downloads_when_size_unknown(self) -> None:
        # 服务器不给 Content-Length 时只能先下再核（这里配额充足）
        self.saver._downloader.probe_bytes = None
        result = self.saver.save_direct("https://zd.shinnku.top/file/unknown.rar", "不明サイズ")
        self.assertTrue(result.ok, result.error)
        self.assertEqual(len(self.saver._downloader.downloaded), 1)

    def test_size_out_of_range_rejected(self) -> None:
        res = Resource(site="ggbase", source_type=SOURCE_MAGNET, title="x",
                       link="magnet:?xt=urn:btih:abc", size_gb=0.01,
                       detail_url="", match_score=90)
        result = self.saver.save(res)
        self.assertFalse(result.ok)
        self.assertIn("不在", result.error)


if __name__ == "__main__":
    unittest.main()
