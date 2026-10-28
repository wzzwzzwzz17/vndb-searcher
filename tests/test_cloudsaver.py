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
    def __init__(self, client, size_bytes: int = 7_460_000_000):
        self.client = client
        self.size_bytes = size_bytes

    def parse(self, link: str) -> dict:
        return {"shareid": "1", "share_uk": "2", "files": [
            {"fs_id": 1, "server_filename": "a.rar", "isdir": 0, "size": self.size_bytes},
        ]}

    def transfer(self, link: str, save_dir: str, info: dict | None = None) -> dict:
        return {"path": save_dir, "files": ["a.rar"], "size_bytes": self.size_bytes}

    def transfer_into(self, link: str, root_dir: str, title: str, info: dict | None = None) -> dict:
        return {"cloud_path": f"{root_dir}/{title}", "files": ["a.rar"],
                "size_bytes": self.size_bytes}


class FakeUploader:
    uploaded: list[tuple[str, str]] = []

    def __init__(self, client, chunk_mb: int = 4, parallel: int = 3):
        self.chunk_mb = chunk_mb
        self.parallel = parallel

    def upload(self, local_path, remote_dir: str, progress=None) -> str:
        FakeUploader.uploaded.append((str(local_path), f"{remote_dir}/x.png"))
        return f"{remote_dir}/x.png"

    def verify(self, remote_path: str, expect_bytes: int, tolerance: float = 0.01) -> bool:
        return True


class FakeClient:
    """假网盘客户端：只提供 baidu_cloud 各模块会碰到的属性。"""

    app_id = "250528"

    def __init__(self) -> None:
        self.dirs: list[str] = []
        self.deleted: list[list[str]] = []
        self.moved: list[tuple[str, str, str]] = []
        # 入库体检会列目录：默认给一个像样的游戏压缩包
        self.files: list[dict] = [{"server_filename": "game.rar", "isdir": 0, "size": 7_460_000_000}]

    def require_login(self) -> "FakeClient":
        return self

    def ensure_dir(self, remote_dir: str) -> str:
        self.dirs.append(remote_dir)
        return remote_dir

    def list_dir(self, remote_dir: str, page: int = 1, num: int = 1000) -> list[dict]:
        return list(self.files)

    def move(self, remote_path: str, dest_dir: str, new_name: str = "") -> bool:
        self.moved.append((remote_path, dest_dir, new_name))
        return True

    def delete(self, paths) -> bool:
        self.deleted.append(list(paths))
        return True


class FakeAria2:
    """假 aria2：直接吐出一个本地文件，记录调用。"""

    def __init__(self, root: Path, size_bytes: int = 3_000_000_000):
        self.root = root
        self.size_bytes = size_bytes
        self.calls: list[str] = []

    def available(self) -> tuple[bool, str]:
        return True, "aria2 version 1.37.0"

    def download(self, magnet: str, name: str, progress=None) -> Path:
        self.calls.append(magnet)
        target = self.root / "aria2" / f"{name}.iso"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"\0" * self.size_bytes)
        return target


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
        # 本机有没有装 aria2 不该影响这些用例：默认走网盘离线分支
        self.saver.magnet_local = False
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
        self.assertIn("ランス10", result.cloud_path)

    def test_share_transfer_does_not_nest_folder_twice(self) -> None:
        # 曾经把标题又拼在目录后面，网盘里变成 v123 名/v123 名/文件
        folder = "/gal_save/v3144 素晴らしき日々～不連続存在～"
        result = self.saver.save_share("https://pan.baidu.com/s/1abc?pwd=t7cp", folder,
                                       "素晴らしき日々～不連続存在～", "kungal")
        self.assertTrue(result.ok)
        self.assertEqual(result.cloud_path, folder)

    def test_flatten_promotes_single_subdir(self) -> None:
        folder = "/gal_save/v1 名"

        class Tree(FakeClient):
            def __init__(self) -> None:
                super().__init__()
                self.tree = {folder: [{"server_filename": "名", "isdir": 1}],
                             "/gal_save": [],
                             f"{folder}/名": [{"server_filename": "a.zip", "isdir": 0, "size": 10}]}

            def list_dir(self, remote_dir: str, page: int = 1, num: int = 1000) -> list[dict]:
                return list(self.tree.get(remote_dir, []))

            def move(self, remote_path: str, dest_dir: str, new_name: str = "") -> bool:
                self.moved.append((remote_path, dest_dir, new_name))
                if remote_path in self.tree:  # 挪走之后源目录就不在了
                    moved = self.tree.pop(remote_path)
                    self.tree[f"{dest_dir}/{new_name}"] = moved
                return True

        client = Tree()
        self.saver._client = client
        self.saver._flatten(folder)
        self.assertEqual(client.moved[0], (folder, "/gal_save", "v1 名 __flatten"))
        self.assertEqual(client.moved[1], (f"{folder} __flatten/名", "/gal_save", "v1 名"))
        self.assertEqual(client.deleted[-1], [f"{folder} __flatten"])

    def test_folder_uses_vndb_id_and_preferred_name(self) -> None:
        # 用户要求：一部作品一个目录，名字带 vndb 编号 + 日文原名
        folder = self.saver.folder_for("v19073", "千恋＊万花")
        self.assertEqual(folder, "/gal_save/v19073 千恋＊万花")

    def test_folder_avoids_double_v_prefix(self) -> None:
        self.assertEqual(self.saver.folder_for("19073", "x"), "/gal_save/v19073 x")

    def test_folder_sanitizes_illegal_chars(self) -> None:
        folder = self.saver.folder_for("v1", 'a/b:c*d?"e"')
        self.assertNotIn("/", folder.rsplit("/", 1)[-1])
        self.assertTrue(folder.startswith("/gal_save/"))

    def test_save_dispatches_with_requested_name(self) -> None:
        res = Resource(site="ggbase", source_type=SOURCE_MAGNET, title="Rance X",
                       link="magnet:?xt=urn:btih:abc", size_gb=4.0, detail_url="", match_score=95)
        result = self.saver.save(res, vndb_id="v20802", name="ランス10－決戦－")
        self.assertTrue(result.ok, result.error)
        self.assertIn("v20802 ランス10－決戦－", result.cloud_path)

    def test_magnet_local_uses_aria2_then_uploads(self) -> None:
        self.saver.magnet_local = True
        fake = FakeAria2(self.root)
        self.saver._aria2 = fake
        result = self.saver.save_magnet("magnet:?xt=urn:btih:abc", "/gal_save/v1 x", "x")
        self.assertTrue(result.ok, result.error)
        self.assertEqual(fake.calls, ["magnet:?xt=urn:btih:abc"])
        self.assertGreater(self.db.c_quota_used_gb(), 0)  # 本机下载要占批次配额
        self.assertEqual(len(FakeUploader.uploaded), 1)
        self.assertEqual(Path(FakeUploader.uploaded[0][0]).suffix, ".png")
        # aria2 的临时目录要清掉，别留垃圾
        self.assertFalse((self.root / "aria2").exists())

    def test_direct_downloads_encrypts_and_uploads(self) -> None:
        result = self.saver.save_direct("https://zd.shinnku.top/file/a.rar", "戦国ランス")
        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.route, SOURCE_DIRECT)
        # 记的是目录（和转存同口径），不是加密包的文件名
        self.assertEqual(result.cloud_path, "戦国ランス")
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

    def test_junk_share_is_deleted_and_rejected(self) -> None:
        # 实测踩到的诱饵：转存下来全是教学视频，一个游戏文件都没有
        def listing(remote_dir: str, page: int = 1, num: int = 1000) -> list[dict]:
            if remote_dir.endswith("第19讲"):
                return [
                    {"server_filename": "1【安卓】.mp4", "isdir": 0, "size": 1_800_000_000},
                    {"server_filename": "2【PC】.mp4", "isdir": 0, "size": 9_020_000_000},
                ]
            return [{"server_filename": "第19讲", "isdir": 1, "size": 0}]

        self.saver._client.list_dir = listing
        result = self.saver.save_share("https://pan.baidu.com/s/1junk?pwd=x", "/gal_save/v1 千恋＊万花", "千恋＊万花")
        self.assertFalse(result.ok)
        self.assertIn("不是游戏本体", result.error)
        # 诱饵目录要整棵删掉，别占着 4TB 的额度
        self.assertIn(["/gal_save/v1 千恋＊万花"], self.saver._client.deleted)

    def test_multiplatform_share_keeps_only_pc(self) -> None:
        def listing(remote_dir: str, page: int = 1, num: int = 1000) -> list[dict]:
            if remote_dir.endswith("PC版"):
                return [
                    {"server_filename": "game.iso", "isdir": 0, "size": 7_000_000_000},
                    {"server_filename": "data.xp3", "isdir": 0, "size": 3_500_000_000},
                ]
            if remote_dir.endswith("安卓直装"):
                return [{"server_filename": "game.apk", "isdir": 0, "size": 4_000_000_000}]
            return [
                {"server_filename": "PC版", "isdir": 1, "size": 0},
                {"server_filename": "安卓直装", "isdir": 1, "size": 0},
            ]

        self.saver._client.list_dir = listing
        result = self.saver.save_share("https://pan.baidu.com/s/1multi?pwd=y", "/gal_save/v1 x", "x")
        self.assertTrue(result.ok, result.error)
        self.assertIn(["/gal_save/v1 x/安卓直装"], self.saver._client.deleted)

    def test_small_share_is_not_pruned(self) -> None:
        self.saver._client.files = [{"server_filename": "game.rar", "isdir": 0, "size": 2_000_000_000}]
        result = self.saver.save_share("https://pan.baidu.com/s/1small?pwd=z", "/gal_save/v2 y", "y")
        self.assertTrue(result.ok, result.error)
        self.assertEqual(self.saver._client.deleted, [])

    def test_prune_can_be_disabled(self) -> None:
        self.saver.prune_enabled = False
        self.saver._client.files = [{"server_filename": "a.mp4", "isdir": 0, "size": 9_000_000_000}]
        result = self.saver.save_share("https://pan.baidu.com/s/1off?pwd=w", "/gal_save/v3 z", "z")
        self.assertTrue(result.ok, result.error)

    def test_demo_only_share_skipped(self) -> None:
        class DemoTransfer(FakeTransfer):
            def parse(self, link: str) -> dict:
                return {"shareid": "1", "share_uk": "2", "files": [
                    {"fs_id": 1, "server_filename": "春ノ雪SAKURAdemo.iso", "isdir": 0, "size": 598_000_000},
                ]}

        with mock.patch.object(cloud_mod, "ShareTransfer", DemoTransfer):
            result = self.saver.save_share(
                "https://pan.baidu.com/s/1demo?pwd=d", "/gal_save/v562 サクラノ詩", "サクラノ詩")
        self.assertFalse(result.ok)
        self.assertIn("试玩版", result.error)
        self.assertEqual(self.saver._client.dirs, [])  # 连目录都不该建

    def test_course_titled_share_skipped(self) -> None:
        class CourseTransfer(FakeTransfer):
            def parse(self, link: str) -> dict:
                return {"shareid": "1", "share_uk": "2", "files": [
                    {"fs_id": 1, "server_filename": "2026真实U3D脚本拆解实践班直播第19讲",
                     "isdir": 1, "size": 0},
                ]}

        with mock.patch.object(cloud_mod, "ShareTransfer", CourseTransfer):
            result = self.saver.save_share(
                "https://pan.baidu.com/s/1course?pwd=q", "/gal_save/v9 x", "千恋＊万花")
        self.assertFalse(result.ok)
        self.assertIn("课程/资料", result.error)

    def test_size_out_of_range_rejected(self) -> None:
        res = Resource(site="ggbase", source_type=SOURCE_MAGNET, title="x",
                       link="magnet:?xt=urn:btih:abc", size_gb=0.01,
                       detail_url="", match_score=90)
        result = self.saver.save(res)
        self.assertFalse(result.ok)
        self.assertIn("不在", result.error)


if __name__ == "__main__":
    unittest.main()
