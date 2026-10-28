# -*- coding: utf-8 -*-
"""分片上传（C 路线的最后一跳）：本地加密后的文件传到百度网盘。

Web 端上传三步：
1. POST /api/precreate  预上传，带每片 MD5 列表；return_type=1 表示秒传成功；
2. POST d.pcs.baidu.com/rest/2.0/pcs/superfile2?method=upload&type=tmpfile&partseq=N
   逐片上传（multipart 字段名 file）；
3. POST /api/create?a=commit  合并分片。
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
from pathlib import Path
from typing import Any, Callable

from .client import PCS, PAN, BaiduError, BaiduClient, posix_name

CHUNK_MB = 4
MAX_CHUNKS_WITH_HASH = 1024  # 太大的文件不给每片算 MD5，省时间


def chunk_md5s(path: str | Path, chunk_size: int) -> list[str]:
    """算每个分片的 MD5（百度预上传要）。"""
    digests: list[str] = []
    with open(path, "rb") as handle:
        while True:
            block = handle.read(chunk_size)
            if not block:
                break
            digests.append(hashlib.md5(block).hexdigest())
    return digests


class Uploader:
    def __init__(self, client: BaiduClient, chunk_mb: int = CHUNK_MB):
        self.client = client
        self.chunk_size = max(1, int(chunk_mb)) * 1024 * 1024
        self.log = _logger()

    def upload(self, local_path: str | Path, remote_dir: str,
               progress: Callable[[int, int], None] | None = None) -> str:
        """上传单个文件到 remote_dir，返回网盘上的完整路径。"""
        path = Path(local_path)
        if not path.is_file():
            raise BaiduError(31066, f"本地文件不存在: {path}", "百度上传")
        size = path.stat().st_size
        if size <= 0:
            raise BaiduError(2, f"文件是空的: {path}", "百度上传")

        remote_dir = self.client.ensure_dir(remote_dir)
        remote_path = f"{remote_dir.rstrip('/')}/{posix_name(str(path))}"

        chunks = max(1, (size + self.chunk_size - 1) // self.chunk_size)
        block_list = chunk_md5s(path, self.chunk_size) if chunks <= MAX_CHUNKS_WITH_HASH else []
        upload_id, rapid = self._precreate(remote_path, size, block_list)
        if rapid:
            self.log.info("秒传成功: %s", remote_path)
            return remote_path

        self._upload_chunks(path, remote_path, upload_id, chunks, progress)
        self._create(remote_path, size, upload_id, chunks)
        return remote_path

    # ---------- 三步 ----------
    def _precreate(self, remote_path: str, size: int, block_list: list[str]) -> tuple[str, bool]:
        params = {
            "path": remote_path,
            "size": str(size),
            "isdir": "0",
            "rtype": "3",
            "block_list": _dump(block_list),
            **self.client.common_params(),
        }
        resp = self.client._post(f"{PAN}/api/precreate", params=params, data={"target_path": remote_path})
        try:
            data = resp.json()
        except ValueError as exc:
            raise BaiduError(-1, "预上传返回不是 JSON", "百度上传") from exc
        errno = data.get("errno")
        if errno not in (0, "0", None):
            raise BaiduError(errno, str(data.get("show_msg") or ""), "百度上传")
        upload_id = str(data.get("uploadid") or "")
        if not upload_id:
            raise BaiduError(-1, "预上传没返回 uploadid", "百度上传")
        return upload_id, int(data.get("return_type") or 0) == 1

    def _upload_chunks(self, path: Path, remote_path: str, upload_id: str, chunks: int,
                       progress: Callable[[int, int], None] | None) -> None:
        url = f"{PCS}/rest/2.0/pcs/superfile2"
        done = 0
        with open(path, "rb") as handle:
            for seq in range(chunks):
                block = handle.read(self.chunk_size)
                if not block:
                    break
                params = {
                    "method": "upload",
                    "type": "tmpfile",
                    "path": remote_path,
                    "uploadid": upload_id,
                    "partseq": str(seq),
                    "app_id": "250528",
                }
                files = {
                    "file": (
                        "blob",
                        block,
                        mimetypes.guess_type(str(path))[0] or "application/octet-stream",
                    )
                }
                resp = self.client.session.post(
                    url, params=params, files=files, timeout=max(self.client.timeout, 120)
                )
                if resp.status_code != 200:
                    raise BaiduError(-1, f"分片 {seq} 上传失败 HTTP {resp.status_code}", "百度上传")
                try:
                    data = resp.json()
                except ValueError:
                    data = {}
                if data.get("errno") not in (None, 0, "0"):
                    raise BaiduError(data.get("errno"), f"第 {seq} 片上传失败", "百度上传")
                done += 1
                if progress:
                    progress(done, chunks)
                if seq == 0 and not data.get("md5"):
                    # 有些账号返回里没有 md5，不影响后续合并
                    pass

    def _create(self, remote_path: str, size: int, upload_id: str, chunks: int) -> None:
        params = {"a": "commit", **self.client.common_params()}
        body = {
            "path": remote_path,
            "size": str(size),
            "isdir": "0",
            "rtype": "3",
            "uploadid": upload_id,
            "block_list": _dump([""] * chunks),
        }
        resp = self.client._post(f"{PAN}/api/create", params=params, data=body)
        try:
            data = resp.json()
        except ValueError as exc:
            raise BaiduError(-1, "合并分片返回不是 JSON", "百度上传") from exc
        errno = data.get("errno")
        if errno not in (0, "0", None):
            raise BaiduError(errno, str(data.get("show_msg") or ""), "百度上传")

    # ---------- 上传后校验 ----------
    def verify(self, remote_path: str, expect_bytes: int, tolerance: float = 0.01) -> bool:
        """确认网盘上的文件大小和本地一致。"""
        try:
            items = self.client.list_dir(_parent(remote_path))
        except BaiduError:
            return False
        name = posix_name(remote_path)
        for item in items:
            if str(item.get("server_filename") or "") == name:
                actual = int(item.get("size") or 0)
                if expect_bytes <= 0:
                    return actual > 0
                return abs(actual - expect_bytes) <= max(1024 * 1024, expect_bytes * tolerance)
        return False


def _dump(items: list[str]) -> str:
    import json

    return json.dumps(items)


def _parent(remote_path: str) -> str:
    from .client import posix_parent

    return posix_parent(remote_path)


def _logger() -> Any:
    import logging

    return logging.getLogger("galsave")


def local_file_mb(path: str | Path) -> float:
    try:
        return round(os.path.getsize(path) / (1024 ** 2), 2)
    except OSError:
        return 0.0
