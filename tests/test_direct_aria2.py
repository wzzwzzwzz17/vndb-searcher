# -*- coding: utf-8 -*-
"""直链的 aria2 分段下载分支（默认关闭，只有 crawler.aria2_direct: true 才走）。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from galsave.downloader.direct import DirectDownloader

BODY = b"0123456789" * 5000  # 50KB


class FakeResp:
    def __init__(self, status: int = 200, headers: dict | None = None, body: bytes = BODY):
        self.status_code = status
        self.headers = headers or {"Content-Length": str(len(body))}
        self._body = body

    def __enter__(self) -> "FakeResp":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def iter_content(self, size: int = 1024):
        for index in range(0, len(self._body), size):
            yield self._body[index:index + size]


class FakeSession:
    """requests 兜底通道：永远整包返回。"""

    headers: dict = {}

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def get(self, url: str, **kwargs: object) -> FakeResp:
        self.calls.append({"url": url, **kwargs})
        headers = kwargs.get("headers") or {}
        if headers.get("Range"):
            return FakeResp(200)  # 假装服务器不支持续传
        return FakeResp(200)

    def head(self, url: str, **kwargs: object) -> FakeResp:
        return FakeResp(200, {"Content-Length": str(len(BODY)), "Accept-Ranges": "bytes"})


def _downloader(tmp: Path, aria2: bool) -> DirectDownloader:
    cfg = {"crawler": {"timeout": 5, "aria2_direct": aria2}}
    return DirectDownloader(cfg, tmp_dir=tmp)


class Aria2DirectTest(unittest.TestCase):
    def test_disabled_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            d = DirectDownloader({"crawler": {"timeout": 5}}, tmp_dir=Path(tmp))
            self.assertFalse(d.use_aria2)

    def test_uses_aria2_when_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            d = _downloader(tmp_path, aria2=True)
            calls: list[str] = []

            def fake_aria2(url, target, referer, retries, progress):
                calls.append(url)
                target.write_bytes(BODY)
                return True

            with mock.patch.object(d, "_aria2_binary", return_value="aria2c"), \
                    mock.patch.object(d, "_download_aria2", side_effect=fake_aria2), \
                    mock.patch.object(d, "probe", return_value=(len(BODY), True)):
                out = d.download("https://example.test/a.rar", "a.rar")
            self.assertEqual(out.read_bytes(), BODY)
            self.assertEqual(calls, ["https://example.test/a.rar"])
            self.assertFalse(d.session.get.call_count if hasattr(d.session, "calls") else 0)

    def test_size_mismatch_falls_back_to_requests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            d = _downloader(tmp_path, aria2=True)
            d.session = FakeSession()

            def fake_aria2(url, target, referer, retries, progress):
                target.write_bytes(b"truncated")  # 体积不对
                return True

            with mock.patch.object(d, "_aria2_binary", return_value="aria2c"), \
                    mock.patch.object(d, "_download_aria2", side_effect=fake_aria2), \
                    mock.patch.object(d, "probe", return_value=(len(BODY), True)):
                out = d.download("https://example.test/a.rar", "a.rar")
            self.assertEqual(out.read_bytes(), BODY)
            self.assertTrue(d.session.calls)

    def test_no_binary_returns_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            d = _downloader(Path(tmp), aria2=True)
            with mock.patch.object(d, "_aria2_binary", return_value=""):
                self.assertFalse(d._download_aria2("https://x/y", Path(tmp) / "y", "", 1, None))


if __name__ == "__main__":
    unittest.main()
