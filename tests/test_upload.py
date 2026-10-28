# -*- coding: utf-8 -*-
"""分片上传：block_list 必须走 body（放 query 会 HTTP 414），大文件要自动放大分片。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

from baidu_cloud import upload as upload_mod
from baidu_cloud.upload import Uploader, chunk_md5s


class FakeJsonResp:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self) -> dict:
        return self._payload


class FakeSession:
    trust_env = True
    headers: dict = {}
    cookies = requests.cookies.RequestsCookieJar()

    def __init__(self) -> None:
        self.chunk_calls: list[dict] = []

    def post(self, url: str, params: dict | None = None, files: dict | None = None,
             timeout: float | None = None) -> FakeJsonResp:
        self.chunk_calls.append(dict(params or {}))
        return FakeJsonResp({"errno": 0, "md5": "x"})


class FakeClient:
    timeout = 30

    def __init__(self) -> None:
        self.session = FakeSession()
        self.posts: list[dict] = []

    def ensure_dir(self, path: str) -> str:
        return path

    def common_params(self) -> dict:
        return {"channel": "chunlei", "web": "1", "app_id": "250528", "bdstoken": "tok"}

    def _post(self, url: str, params: dict | None = None, data: dict | None = None, **kw: object) -> FakeJsonResp:
        self.posts.append({"url": url, "params": dict(params or {}), "data": dict(data or {})})
        if url.endswith("/api/precreate"):
            return FakeJsonResp({"errno": 0, "uploadid": "UID123", "return_type": 0})
        return FakeJsonResp({"errno": 0})

    def list_dir(self, path: str, page: int = 1, num: int = 1000) -> list[dict]:
        return [{"server_filename": "a.bin", "isdir": 0, "size": 1024}]


class UploadTest(unittest.TestCase):
    def _upload(self, size: int, chunk_mb: int) -> tuple[FakeClient, list[str]]:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "payload.png"
            path.write_bytes(b"\x01" * size)
            client = FakeClient()
            up = Uploader(client, chunk_mb=chunk_mb)  # type: ignore[arg-type]
            remote = up.upload(path, "/gal_save/v1 名")
            return client, [remote]

    def test_block_list_stays_in_query(self) -> None:
        client, _ = self._upload(2 * 1024 * 1024 + 5, chunk_mb=1)
        precreate = [p for p in client.posts if p["url"].endswith("/api/precreate")][0]
        # 实测：block_list 放 body 会让后续分片被服务器掐断，只能放 query
        self.assertIn("block_list", precreate["params"])
        self.assertNotIn("block_list", precreate["data"])
        self.assertEqual(len(json.loads(precreate["params"]["block_list"])), 3)
        self.assertEqual(precreate["data"]["target_path"], "/gal_save/v1 名/payload.png")

    def test_commit_sends_real_md5s(self) -> None:
        client, _ = self._upload(2 * 1024 * 1024 + 5, chunk_mb=1)
        commit = [p for p in client.posts if p["url"].endswith("/api/create")][0]
        blocks = json.loads(commit["data"]["block_list"])
        self.assertEqual(len(blocks), 3)
        self.assertTrue(all(len(b) == 32 for b in blocks))  # 不是空串占位

    def test_big_file_raises_chunk_size(self) -> None:
        with mock.patch.object(upload_mod, "MAX_BLOCKS", 2):
            client, _ = self._upload(3 * 1024 * 1024, chunk_mb=1)
        precreate = [p for p in client.posts if p["url"].endswith("/api/precreate")][0]
        blocks = json.loads(precreate["params"]["block_list"])
        self.assertEqual(len(blocks), 2)  # 1MB×3 会被合并成 2MB×2 片
        self.assertEqual(len(client.session.chunk_calls), 2)

    def test_chunk_upload_retries_on_connection_reset(self) -> None:
        class FlakySession(FakeSession):
            def post(self, url: str, params: dict | None = None, files: dict | None = None,
                     timeout: float | None = None) -> FakeJsonResp:
                self.chunk_calls.append(dict(params or {}))
                if len(self.chunk_calls) == 1:
                    raise ConnectionResetError(10054, "远程主机强迫关闭了一个现有的连接")
                return FakeJsonResp({"errno": 0})

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.bin"
            path.write_bytes(b"\x02" * (2 * 1024 * 1024))
            client = FakeClient()
            client.session = FlakySession()

            def fake_post_chunk(cl, url, params, files, attempt):
                # 重试时实际会换新的 TLS 会话，测试里仍走假会话，避免真联网
                return cl.session.post(url, params=params, files=files)

            with mock.patch.object(upload_mod, "_post_chunk", fake_post_chunk), \
                    mock.patch.object(upload_mod.time, "sleep", lambda *_: None):
                Uploader(client, chunk_mb=1).upload(path, "/gal_save/v1 名")  # type: ignore[arg-type]
        self.assertEqual(len(client.session.chunk_calls), 3)  # 第一次失败后重试了同一片

    def test_loose_session_lowers_tls_level(self) -> None:
        client = FakeClient()
        session = upload_mod._loose_ssl_session(client)  # type: ignore[arg-type]
        self.assertEqual(session.trust_env, client.session.trust_env)  # 跟随主会话的代理设置
        session.close()

    def test_chunk_md5s_matches_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.bin"
            path.write_bytes(b"a" * 10)
            self.assertEqual(len(chunk_md5s(path, 4)), 3)


if __name__ == "__main__":
    unittest.main()
