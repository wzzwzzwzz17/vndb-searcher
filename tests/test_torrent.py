# -*- coding: utf-8 -*-
"""磁力本地下（aria2）的离线测试：不真的联网、不真的调 aria2。"""

from __future__ import annotations

import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from galsave.config import load_config
from galsave.downloader.torrent import Aria2Downloader, Aria2Error, _tag, cleanup_dir


class TorrentHelperTest(unittest.TestCase):
    def test_tag_strips_illegal_chars(self) -> None:
        self.assertEqual(_tag('a/b:c*d?"e"'), "a_b_c_d__e_")
        self.assertEqual(_tag(""), "magnet")
        self.assertEqual(len(_tag("x" * 200)), 60)

    def test_cleanup_dir_removes_file_and_empty_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "work"
            work.mkdir()
            target = work / "a.zip"
            target.write_bytes(b"x")
            cleanup_dir(target)
            self.assertFalse(target.exists())
            self.assertFalse(work.exists())  # 空目录也要清掉

    def test_cleanup_dir_keeps_non_empty_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "work"
            work.mkdir()
            target = work / "a.zip"
            target.write_bytes(b"x")
            (work / "other.bin").write_bytes(b"y")
            cleanup_dir(target)
            self.assertFalse(target.exists())
            self.assertTrue(work.exists())


class Aria2DownloaderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.cfg = load_config()
        self.cfg["baidu"]["aria2_path"] = str(self.root / "no-such-aria2c")

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_missing_binary_reports_reason(self) -> None:
        dl = Aria2Downloader(self.cfg, tmp_dir=self.root / "tmp")
        ok, why = dl.available()
        self.assertFalse(ok)
        self.assertIn("aria2c", why)

    def test_download_refuses_non_magnet(self) -> None:
        dl = Aria2Downloader(self.cfg, tmp_dir=self.root / "tmp")
        with self.assertRaises(Aria2Error):
            dl.download("https://example.com/a.zip", "x")

    def test_download_without_binary_raises(self) -> None:
        dl = Aria2Downloader(self.cfg, tmp_dir=self.root / "tmp")
        with self.assertRaises(Aria2Error) as ctx:
            dl.download("magnet:?xt=urn:btih:abc", "x")
        self.assertIn("aria2c", str(ctx.exception))

    def test_package_single_file_returned_as_is(self) -> None:
        dl = Aria2Downloader(self.cfg, tmp_dir=self.root / "tmp")
        work = self.root / "work"
        work.mkdir()
        iso = work / "game.iso"
        iso.write_bytes(b"0" * 1024)
        self.assertEqual(dl._package(work, [iso], "作品"), iso)

    def test_package_multiple_files_into_zip(self) -> None:
        dl = Aria2Downloader(self.cfg, tmp_dir=self.root / "tmp")
        work = self.root / "work"
        sub = work / "cd1"
        sub.mkdir(parents=True)
        a = work / "game.iso"
        b = sub / "track.flac"
        a.write_bytes(b"0" * 100)
        b.write_bytes(b"1" * 100)
        out = dl._package(work, [a, b], "作品")
        self.assertEqual(out.suffix, ".zip")
        with zipfile.ZipFile(out) as zf:
            names = sorted(zf.namelist())
        self.assertEqual(names, ["cd1/track.flac", "game.iso"])
        # 原始文件打包后要删掉，别占磁盘
        self.assertFalse(a.exists())
        self.assertFalse(b.exists())


class Aria2WatchdogTest(unittest.TestCase):
    """aria2 下完不肯退出的情况（实测会挂住），看门狗要能收工。"""

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.cfg = load_config()
        self.cfg["baidu"]["aria2_path"] = str(self.root / "no-such-aria2c")
        self.cfg["baidu"]["aria2_stall_seconds"] = 2

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def _runner(self, writes: str) -> list[str]:
        script = (
            "import pathlib, time\n"
            f"target = pathlib.Path(r'{self.root / 'work' / 'game.iso'}')\n"
            f"{writes}\n"
            "time.sleep(600)\n"
        )
        return [sys.executable, "-c", script]

    def test_returns_when_files_complete_but_process_hangs(self) -> None:
        import time

        work = self.root / "work"
        work.mkdir()
        dl = Aria2Downloader(self.cfg, tmp_dir=self.root / "tmp")
        started = time.time()
        code = dl._run_aria2(self._runner("target.write_bytes(b'x' * 4096)"), work)
        self.assertEqual(code, 0)
        self.assertLess(time.time() - started, 60)
        self.assertEqual((work / "game.iso").stat().st_size, 4096)

    def test_gives_up_when_nothing_downloaded(self) -> None:
        work = self.root / "work"
        work.mkdir()
        dl = Aria2Downloader(self.cfg, tmp_dir=self.root / "tmp")
        code = dl._run_aria2(self._runner("pass"), work)
        self.assertEqual(code, 1)

    def test_gives_up_when_leftovers_are_incomplete(self) -> None:
        work = self.root / "work"
        work.mkdir()
        dl = Aria2Downloader(self.cfg, tmp_dir=self.root / "tmp")
        script = self._runner("target.write_bytes(b'x'); (target.parent / 'game.iso.aria2').write_bytes(b'c')")
        self.assertEqual(dl._run_aria2(script, work), 1)


class MagnetTrackerTest(unittest.TestCase):
    def test_trackers_are_appended_when_missing(self) -> None:
        dl = Aria2Downloader(load_config(), tmp_dir=Path(tempfile.mkdtemp()))
        out = dl._with_trackers("magnet:?xt=urn:btih:abc")
        self.assertIn("&tr=udp%3A%2F%2Ftracker.opentrackr.org%3A1337%2Fannounce", out)

    def test_existing_trackers_are_kept(self) -> None:
        dl = Aria2Downloader(load_config(), tmp_dir=Path(tempfile.mkdtemp()))
        link = "magnet:?xt=urn:btih:abc&tr=udp%3A%2F%2Fmine%3A1%2Fannounce"
        self.assertEqual(dl._with_trackers(link), link)


if __name__ == "__main__":
    unittest.main()
