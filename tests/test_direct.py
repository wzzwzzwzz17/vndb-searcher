# -*- coding: utf-8 -*-
"""直链下载器：断流续传、忽略 Range 的服务器、.part 保留。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import requests

from galsave.downloader.direct import DirectDownloader, DownloadError, safe_filename

BODY = bytes(range(256)) * 40  # 10240 字节，够分几次 chunk


class FakeResp:
    def __init__(self, status: int, headers: dict | None = None, body: bytes = b"",
                 break_after: int | None = None):
        self.status_code = status
        self.headers = headers or {}
        self._body = body
        self._break_after = break_after

    def __enter__(self) -> "FakeResp":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def iter_content(self, size: int = 1024):
        step = min(size, 1000)  # 小步产出，方便在中间制造断流
        sent = 0
        for index in range(0, len(self._body), step):
            if self._break_after is not None and sent >= self._break_after:
                raise requests.exceptions.ChunkedEncodingError(
                    f"Connection broken: IncompleteRead({sent} bytes read, "
                    f"{len(self._body) - sent} more expected)"
                )
            chunk = self._body[index:index + step]
            sent += len(chunk)
            yield chunk


class ScriptedSession:
    """按脚本返回响应，记录每次请求的 Range 头。"""

    def __init__(self, script: list):
        self.script = script
        self.calls: list[dict] = []

    def get(self, url: str, headers: dict | None = None, stream: bool = False,
            timeout: float | None = None) -> FakeResp:
        self.calls.append(dict(headers or {}))
        index = min(len(self.calls) - 1, len(self.script) - 1)
        return self.script[index](dict(headers or {}))


def make_downloader(session: ScriptedSession, tmp: str) -> DirectDownloader:
    dl = DirectDownloader({"crawler": {"timeout": 5}}, tmp_dir=Path(tmp))
    dl.session = session  # type: ignore[assignment]
    dl.log = _quiet()
    return dl


class _Quiet:
    def info(self, *a: object, **k: object) -> None:
        pass

    def debug(self, *a: object, **k: object) -> None:
        pass

    def warning(self, *a: object, **k: object) -> None:
        pass


def _quiet() -> _Quiet:
    return _Quiet()


class DownloadResumeTest(unittest.TestCase):
    def test_plain_download(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = ScriptedSession([
                lambda h: FakeResp(200, {"Content-Length": str(len(BODY))}, BODY),
            ])
            dl = make_downloader(session, tmp)
            path = dl.download("https://cdn.example.com/a.bin", "游戏 本体.rar", retries=1)
            self.assertEqual(path.read_bytes(), BODY)
            self.assertFalse(path.with_suffix(path.suffix + ".part").exists())
            self.assertTrue(path.name.endswith(".rar"))

    def test_resumes_from_part_with_range(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / safe_filename("game.zip")
            part = target.with_suffix(target.suffix + ".part")
            part.write_bytes(BODY[:4000])

            def respond(headers: dict) -> FakeResp:
                self.assertEqual(headers.get("Range"), "bytes=4000-")
                rest = BODY[4000:]
                return FakeResp(206, {
                    "Content-Range": f"bytes 4000-{len(BODY) - 1}/{len(BODY)}",
                    "Content-Length": str(len(rest)),
                }, rest)

            dl = make_downloader(ScriptedSession([respond]), tmp)
            path = dl.download("https://cdn.example.com/game.zip", "game.zip", retries=1)
            self.assertEqual(path.read_bytes(), BODY)

    def test_server_ignores_range_restarts_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / safe_filename("game.zip")
            part = target.with_suffix(target.suffix + ".part")
            part.write_bytes(b"x" * 999)  # 坏的半截文件

            dl = make_downloader(ScriptedSession([
                lambda h: FakeResp(200, {"Content-Length": str(len(BODY))}, BODY),
            ]), tmp)
            path = dl.download("https://cdn.example.com/game.zip", "game.zip", retries=1)
            self.assertEqual(path.read_bytes(), BODY)  # 没有重复追加

    def test_broken_stream_resumes_and_finishes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first = FakeResp(200, {"Content-Length": str(len(BODY))}, BODY, break_after=4096)

            def second(headers: dict) -> FakeResp:
                start = int(headers.get("Range", "bytes=0-").split("=")[1].split("-")[0])
                rest = BODY[start:]
                return FakeResp(206, {
                    "Content-Range": f"bytes {start}-{len(BODY) - 1}/{len(BODY)}",
                    "Content-Length": str(len(rest)),
                }, rest)

            dl = make_downloader(ScriptedSession([lambda h: first, second]), tmp)
            path = dl.download("https://cdn.example.com/game.zip", "game.zip", retries=3)
            self.assertEqual(path.read_bytes(), BODY)

    def test_completed_file_is_reused_without_redownloading(self) -> None:
        """上次下好但上传失败的文件，重试时不该再下一遍（省校园流量）。"""
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / safe_filename("game.zip")
            target.write_bytes(BODY)

            class HeadOnly(ScriptedSession):
                def head(self, url, timeout=None, headers=None, allow_redirects=True):
                    return FakeResp(200, {"Content-Length": str(len(BODY)),
                                          "Accept-Ranges": "bytes"}, b"")

            session = HeadOnly([])
            dl = make_downloader(session, tmp)
            path = dl.download("https://cdn.example.com/game.zip", "game.zip", retries=1)
            self.assertEqual(path.read_bytes(), BODY)
            self.assertEqual(session.calls, [])  # 一次 GET 都没发

    def test_truncated_file_is_not_reused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / safe_filename("game.zip")
            target.write_bytes(BODY[:1000])

            class HeadOnly(ScriptedSession):
                def head(self, url, timeout=None, headers=None, allow_redirects=True):
                    return FakeResp(200, {"Content-Length": str(len(BODY))}, b"")

            session = HeadOnly([lambda h: FakeResp(200, {"Content-Length": str(len(BODY))}, BODY)])
            dl = make_downloader(session, tmp)
            path = dl.download("https://cdn.example.com/game.zip", "game.zip", retries=1)
            self.assertEqual(path.read_bytes(), BODY)  # 重新下完整
            self.assertEqual(len(session.calls), 1)

    def test_partial_target_is_renamed_and_resumed(self) -> None:
        # aria2 下到一半留下的整名文件：要当 .part 接着续，不能从头再下一遍
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / safe_filename("game.zip")
            target.write_bytes(BODY[:3000])

            class HeadOnly(ScriptedSession):
                def head(self, url, timeout=None, headers=None, allow_redirects=True):
                    return FakeResp(200, {"Content-Length": str(len(BODY)),
                                          "Accept-Ranges": "bytes"}, b"")

                def get(self, url, headers=None, stream=False, timeout=None):
                    self.calls.append(dict(headers or {}))
                    start = int((headers or {}).get("Range", "bytes=0-").split("=")[1].split("-")[0])
                    rest = BODY[start:]
                    return FakeResp(206, {
                        "Content-Range": f"bytes {start}-{len(BODY) - 1}/{len(BODY)}",
                        "Content-Length": str(len(rest)),
                    }, rest)

            session = HeadOnly([])
            dl = make_downloader(session, tmp)
            path = dl.download("https://cdn.example.com/game.zip", "game.zip", retries=1)
            self.assertEqual(path.read_bytes(), BODY)
            self.assertEqual(session.calls[0].get("Range"), "bytes=3000-")

    def test_all_attempts_broken_keeps_part(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            broken = lambda h: FakeResp(200, {"Content-Length": str(len(BODY))}, BODY, break_after=2048)  # noqa: E731
            dl = make_downloader(ScriptedSession([broken]), tmp)
            with self.assertRaises(DownloadError):
                dl.download("https://cdn.example.com/game.zip", "game.zip", retries=2)
            part = Path(tmp) / (safe_filename("game.zip") + ".part")
            self.assertTrue(part.exists())
            self.assertGreater(part.stat().st_size, 0)
            self.assertLess(part.stat().st_size, len(BODY))


class SafeFilenameTest(unittest.TestCase):
    def test_strips_illegal_chars(self) -> None:
        self.assertEqual(safe_filename('a/b:c*?"d?.rar'), "a_b_c___d_.rar")

    def test_empty_falls_back(self) -> None:
        self.assertEqual(safe_filename("   "), "download.bin")


if __name__ == "__main__":
    unittest.main()
